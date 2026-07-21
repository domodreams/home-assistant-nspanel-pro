"""Panel siren.

Exposes the NSPanel as a HA ``siren``: HA turns it on/off (optionally choosing a
``tone``) and the integration forwards the command over ``cmd/siren`` — the app
loops the matching bundled sound (``res/raw/*``). State is OPTIMISTIC (the panel
has no siren-state echo topic in v1). The panel ALSO auto-sounds the siren
locally when Alarmo reports a ``triggered`` state (app-side, over Alarmo's native
MQTT), so this entity is one of two independent triggers — both drive the same
looping player.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.siren import SirenEntity
from homeassistant.components.siren.const import ATTR_TONE, SirenEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PanelConfigEntry
from .bridge import PanelBridge
from .entity import DomoPanelEntity

#: Bundled res/raw sounds the app can loop, exposed as selectable tones. The
#: default (first) is the wailing siren; HA/Alarmo may pick another.
SIREN_TONES = ["siren", "alarmo", "alarm_beep", "alarm"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DomoSirenEntity(entry.runtime_data)])


class DomoSirenEntity(DomoPanelEntity, SirenEntity):
    """Plays a looping sound on the panel (via ``cmd/siren``)."""

    _attr_name = "Siren"
    _attr_icon = "mdi:alarm-light"
    _attr_supported_features = (
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.TONES
    )
    _attr_available_tones = SIREN_TONES

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_siren"
        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        payload: dict[str, Any] = {"on": True}
        tone = kwargs.get(ATTR_TONE)
        if tone:
            payload["tone"] = tone
        await self.bridge.async_cmd("siren", payload)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.bridge.async_cmd("siren", {"on": False})
        self._is_on = False
        self.async_write_ha_state()
