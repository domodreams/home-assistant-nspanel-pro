"""Giving the panel's player something to throw away.

**The problem, measured on the device (2026-07-21).** The NSPanel's MediaPlayer
drops roughly the last 0.3 s of EVERY clip it plays: a 1.63 s TTS phrase is heard
as "…il ci" instead of "…il ciclo", a 2.12 s one stops at "…, gra", and even the
bundled 18.55 s ``alarmo.mp3`` — which never goes near a network or a TTS engine
— comes back ~0.3 s short. It is not the streaming proxy (a static file served
from a laptop behaves identically), not ``react-native-sound-player`` (its
completion handler only emits an event), and not Home Assistant.

**Why TTS suffers most.** Piper renders a clip that ends exactly on the last
sample: ffmpeg finds no trailing silence anywhere in it, and no punctuation
trick adds any (``.``, ``...``, ``, ,`` all render the same length). So the
player's missing tail comes straight out of the final word.

**The fix.** Append silence to the clip before the panel ever sees it, so the
0.3 s it discards is silence instead of a syllable. MP3 frames are
self-describing and simply concatenate, so this is a byte append — no ffmpeg at
runtime, no re-encode, no measurable delay.

The padded clip is served from memory by :class:`PanelClipView` under a random
token, exactly like HA's own ``tts_proxy`` serves unauthenticated audio. It is
short-lived by design: the panel plays on receipt and never re-fetches, so
nothing here needs to survive a restart.
"""

from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

#: ~1 s of digital silence, 22050 Hz mono — piper's own format, so the appended
#: frames match the clip they follow. Generated once with ffmpeg; see the module
#: docstring for why it exists at all.
_SILENCE = Path(__file__).parent / "sounds" / "silence.mp3"

URL_BASE = "/api/domodreams_panel/clip"

#: A clip is fetched once, seconds after it is minted. Keep a handful for the
#: case of several panels, and forget them quickly — this is audio in RAM.
_MAX_CLIPS = 8
_TTL = 300

#: Don't pad something that is not an MP3 frame stream (a wav from another TTS
#: engine, say): appending mp3 frames to it would corrupt the file.
_MP3_MAGIC = (b"\xff", b"ID3")

_DATA_KEY = f"{__name__}_clips"


def _clips(hass: HomeAssistant) -> dict[str, tuple[bytes, float]]:
    return hass.data.setdefault(_DATA_KEY, {})


@callback
def async_register_clip_view(hass: HomeAssistant) -> None:
    """Register the padded-clip endpoint once (idempotent)."""
    if hass.data.get(f"{_DATA_KEY}_view"):
        return
    hass.http.register_view(PanelClipView)
    hass.data[f"{_DATA_KEY}_view"] = True


def _silence() -> bytes | None:
    try:
        return _SILENCE.read_bytes()
    except OSError as err:  # pragma: no cover - packaging accident
        _LOGGER.warning("padding disabled: cannot read %s (%s)", _SILENCE, err)
        return None


async def async_padded_url(hass: HomeAssistant, url: str) -> str:
    """Return a URL for the same audio with silence appended.

    Falls back to `url` unchanged on ANY problem — a notification that speaks
    with a clipped tail is still far better than one that does not speak.
    """
    silence = await hass.async_add_executor_job(_silence)
    if not silence:
        return url

    try:
        session = async_get_clientsession(hass)
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                _LOGGER.warning("padding skipped: %s answered %s", url, resp.status)
                return url
            clip = await resp.read()
    except Exception as err:  # noqa: BLE001 - never fail the notification
        _LOGGER.warning("padding skipped: could not fetch the clip (%s)", err)
        return url

    if not clip.startswith(_MP3_MAGIC):
        _LOGGER.debug("padding skipped: not an mp3 stream")
        return url

    token = secrets.token_urlsafe(24)
    clips = _clips(hass)
    now = time.monotonic()
    # Evict expired first, then the oldest if still over the cap.
    for key in [k for k, (_, exp) in clips.items() if exp <= now]:
        clips.pop(key, None)
    while len(clips) >= _MAX_CLIPS:
        clips.pop(min(clips, key=lambda k: clips[k][1]), None)
    clips[token] = (clip + silence, now + _TTL)

    # Same helper the media_player uses: makes the path absolute against the
    # LAN url the panel can actually reach.
    return async_process_play_media_url(hass, f"{URL_BASE}/{token}.mp3")


class PanelClipView(HomeAssistantView):
    """Serve one padded clip. Unauthenticated behind a random token, exactly
    like HA's own tts_proxy — the panel has no Home Assistant credentials."""

    url = URL_BASE + "/{token}.mp3"
    name = "api:domodreams_panel:clip"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry: tuple[bytes, float] | None = _clips(hass).get(token)
        if entry is None or entry[1] <= time.monotonic():
            return web.Response(status=404)
        return web.Response(body=entry[0], content_type="audio/mpeg")


def clip_count(hass: HomeAssistant) -> int:
    """Diagnostics/tests: how many padded clips are currently held."""
    return len(_clips(hass))
