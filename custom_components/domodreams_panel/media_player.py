"""The panel as a HA ``media_player`` — so HA can play audio ON the device.

This is what makes ``tts.speak``, ``media_player.play_media`` and Music
Assistant (through its "Home Assistant players" provider) able to target the
panel like any other speaker.

Protocol (v2.2): commands go out on ``cmd/media`` (non-retained), and the panel
answers on the retained ``sys/media`` — so HA rebuilds the entity's state after
a restart from the broker alone, with the panel never being asked anything.

Two things learned the hard way on the physical panel, both load-bearing:

1. HA hands us ``media-source://…`` ids, never a playable URL. They must be
   resolved AND turned into an absolute, signed http:// URL the panel can fetch
   over the LAN (``async_process_play_media_url``). This instance has no
   ``internal_url`` configured, so that helper falls back to HA's LAN IP, which
   is exactly what the panel needs — but if someone ever sets an external/cloud
   URL, TTS would route the panel's audio through the internet and back.

2. TTS URLs are SHORT-LIVED: HA evicts clips from its tts_proxy cache, and a
   stale one answers 404. The panel therefore plays on receipt and never
   re-fetches a URL later — a queued/retried URL would simply be gone.

Announce (play over the current content, then resume) is NOT advertised: the
app still has a single shared player, so it cannot be honoured honestly. It
arrives with the native player, together with pause/seek accuracy.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import PanelConfigEntry
from .bridge import PanelBridge
from .const import signal_media
from .entity import DomoPanelEntity

#: panel ``sys/media.state`` → HA state. Anything unknown reads as idle.
_STATE_MAP = {
    "idle": MediaPlayerState.IDLE,
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DomoMediaPlayerEntity(entry.runtime_data)])


class DomoMediaPlayerEntity(DomoPanelEntity, MediaPlayerEntity):
    """Speaker entity backed by the panel's own audio output."""

    _attr_name = "Speaker"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_media"
        self._media = dict(bridge.media)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_media(self.bridge.device_id), self._media_updated
            )
        )

    @callback
    def _media_updated(self, media: dict[str, Any]) -> None:
        self._media = media
        self.async_write_ha_state()

    # --- state ---------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        return _STATE_MAP.get(str(self._media.get("state")), MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        volume = self._media.get("volume")
        return float(volume) if isinstance(volume, (int, float)) else None

    @property
    def is_volume_muted(self) -> bool | None:
        muted = self._media.get("muted")
        return bool(muted) if isinstance(muted, bool) else None

    @property
    def media_title(self) -> str | None:
        title = self._media.get("title")
        if isinstance(title, str) and title:
            return title
        url = self._media.get("url")
        if isinstance(url, str) and url:
            # TTS clips are served under /api/tts_proxy/<hash>.mp3 — the hash is
            # noise on a media card, and tts.speak passes no title.
            if "/api/tts_proxy/" in url:
                return "Text-to-speech"
            # Fall back to the file name. Drop the query first: media-source URLs
            # carry a signed `?authSig=<JWT>`, which would otherwise be shown in
            # full as the track title.
            name = urlparse(url).path.rsplit("/", 1)[-1]
            return unquote(name) or None
        asset = self._media.get("asset")
        return asset if isinstance(asset, str) else None

    @property
    def media_duration(self) -> int | None:
        duration = self._media.get("durationS")
        return int(duration) if isinstance(duration, (int, float)) else None

    @property
    def media_position(self) -> int | None:
        position = self._media.get("positionS")
        return int(position) if isinstance(position, (int, float)) else None

    @property
    def media_position_updated_at(self):
        """When ``media_position`` was sampled ON THE PANEL.

        The panel publishes position only on transitions; HA extrapolates a
        moving position from this timestamp, so a playing panel does not have to
        publish once a second (it has 1 GB of RAM and a WebSocket MQTT link).
        """
        ts = self._media.get("positionTs")
        if not isinstance(ts, (int, float)):
            return None
        return dt_util.utc_from_timestamp(ts / 1000)

    # --- commands ------------------------------------------------------------

    async def async_media_play(self) -> None:
        await self.bridge.async_cmd("media", {"action": "resume"})

    async def async_media_pause(self) -> None:
        await self.bridge.async_cmd("media", {"action": "pause"})

    async def async_media_stop(self) -> None:
        await self.bridge.async_cmd("media", {"action": "stop"})

    async def async_set_volume_level(self, volume: float) -> None:
        await self.bridge.async_cmd("media", {"action": "volume", "volume": volume})

    async def async_mute_volume(self, mute: bool) -> None:
        await self.bridge.async_cmd("media", {"action": "mute", "muted": mute})

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play whatever HA hands us — TTS, local media, or a plain URL."""
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = item.url
        payload: dict[str, Any] = {
            "action": "play",
            "url": async_process_play_media_url(self.hass, media_id),
        }
        title = kwargs.get("extra", {}).get("title")
        if title:
            payload["title"] = title
        await self.bridge.async_cmd("media", payload)

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )
