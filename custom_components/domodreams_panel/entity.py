"""Shared entity base — device linkage + availability from ``avail``."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .bridge import PanelBridge
from .const import signal_avail


class DomoPanelEntity(Entity):
    """Base: links to the panel device and tracks availability."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, bridge: PanelBridge) -> None:
        self.bridge = bridge
        self._attr_device_info = bridge.device_info

    @property
    def available(self) -> bool:
        return self.bridge.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_avail(self.bridge.device_id),
                self._avail_updated,
            )
        )

    @callback
    def _avail_updated(self, _available: bool) -> None:
        self.async_write_ha_state()
