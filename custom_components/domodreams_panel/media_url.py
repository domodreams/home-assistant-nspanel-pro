"""Turning what HA hands us into a URL the panel can actually fetch.

Extracted from ``media_player.async_play_media``, which learned all of this the
hard way on the physical panel, and now shared with the notification service so
a notification sound and a ``media_player.play_media`` call resolve identically.

Two rules worth keeping in one place:

1. HA gives out ``media-source://…`` ids, never a playable URL. They have to be
   resolved AND turned into an absolute, signed ``http://`` URL the panel can
   fetch over the LAN (``async_process_play_media_url``). This instance has no
   ``internal_url`` set, so that helper falls back to HA's LAN IP — exactly what
   the panel needs. If someone ever configures an external/cloud URL, the panel's
   audio would route through the internet and back.

2. TTS urls are SHORT-LIVED: HA evicts clips from its ``tts_proxy`` cache and a
   stale one answers 404 — which is precisely what a silent panel looks like.
   Everything here resolves at the moment of use; nothing is ever cached for
   later, and the panel plays on receipt.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import media_source, tts
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .padded_audio import async_padded_url


async def async_resolve_media_url(
    hass: HomeAssistant, media_id: str, entity_id: str | None = None
) -> str:
    """Resolve any media id/url to something the panel can play right now."""
    if media_source.is_media_source_id(media_id):
        item = await media_source.async_resolve_media(hass, media_id, entity_id)
        media_id = item.url
    return async_process_play_media_url(hass, media_id)


async def async_speech_url(
    hass: HomeAssistant,
    message: str,
    engine: str | None = None,
    language: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    """Render ``message`` to speech and return a URL the panel can fetch.

    Same path ``tts.speak`` takes — a TTS media-source id, resolved to a signed
    LAN url. With no engine given, HA's default is used; if the instance has no
    TTS engine at all, that is a configuration problem worth naming, not a
    silent no-op.

    The clip is then PADDED with silence (``padded_audio``): this panel's player
    discards the last ~0.3 s of everything it plays, and a TTS clip ends exactly
    on its final sample, so without the padding the last word is what gets
    discarded. Verified by ear: "…il ciclo" arrived as "…il ci".
    """
    resolved = engine or tts.async_default_engine(hass)
    if not resolved:
        raise ServiceValidationError(
            "No text-to-speech engine is configured in Home Assistant"
        )
    media_id = tts.generate_media_source_id(
        hass, message, engine=resolved, language=language, options=options
    )
    url = await async_resolve_media_url(hass, media_id)
    return await async_padded_url(hass, url)
