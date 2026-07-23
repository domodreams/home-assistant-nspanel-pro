"""Awake binary_sensor (from ``sys/awake``)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PanelConfigEntry
from .bridge import PanelBridge
from .const import signal_awake, signal_motion
from .entity import DomoPanelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [DomoAwakeSensor(entry.runtime_data), DomoMotionSensor(entry.runtime_data)]
    )


class DomoAwakeSensor(DomoPanelEntity, BinarySensorEntity):
    """True while the panel screen is awake (not in the screensaver)."""

    _attr_name = "Awake"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:television-ambient-light"

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_awake"

    @property
    def is_on(self) -> bool | None:
        if self.bridge.awake is None:
            return None
        return bool(self.bridge.awake.get("awake"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.bridge.awake is None:
            return {}
        return {"cause": self.bridge.awake.get("cause")}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_awake(self.bridge.device_id),
                self._awake_updated,
            )
        )

    @callback
    def _awake_updated(self, _data: dict[str, Any]) -> None:
        self.async_write_ha_state()


class DomoMotionSensor(DomoPanelEntity, BinarySensorEntity):
    """Near-field presence at the panel, from the proximity sensor (sys/motion).

    Usable as an Alarmo trigger sensor. ~30cm range; clears a few seconds after
    the person steps away (debounced app-side)."""

    _attr_name = "Motion"
    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_motion"

    @property
    def is_on(self) -> bool | None:
        return self.bridge.motion

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_motion(self.bridge.device_id),
                self._motion_updated,
            )
        )

    @callback
    def _motion_updated(self, _motion: bool) -> None:
        self.async_write_ha_state()
