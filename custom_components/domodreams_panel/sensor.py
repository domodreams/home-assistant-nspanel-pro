"""Diagnostic sensors: illuminance, RSSI, IP, uptime, free memory, serial, the
two version strings (app + Sonoff firmware), and the licence.

Illuminance tracks ``sys/light``, the licence tracks ``sys/license``, and the
rest track ``sys/info``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import PanelConfigEntry
from .bridge import PanelBridge
from .const import signal_info, signal_license, signal_light
from .entity import DomoPanelEntity


@dataclass(frozen=True, kw_only=True)
class PanelSensor:
    key: str
    name: str
    value: Callable[[PanelBridge], Any]
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    unit: str | None = None
    icon: str | None = None
    diagnostic: bool = True


def _info(bridge: PanelBridge, field: str) -> Any:
    return bridge.sys_info.get(field)


INFO_SENSORS: tuple[PanelSensor, ...] = (
    PanelSensor(
        key="rssi",
        name="Wi-Fi signal",
        value=lambda b: _info(b, "rssi"),
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        unit=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    ),
    PanelSensor(
        key="ip",
        name="IP address",
        value=lambda b: _info(b, "ip"),
        icon="mdi:ip-network",
    ),
    PanelSensor(
        key="uptime",
        name="Uptime",
        value=lambda b: _info(b, "uptimeS"),
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTime.SECONDS,
    ),
    PanelSensor(
        key="free_mem",
        name="Free memory",
        value=lambda b: _info(b, "freeMemMB"),
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfInformation.MEGABYTES,
        icon="mdi:memory",
    ),
    PanelSensor(
        key="app_version",
        name="App version",
        value=lambda b: _info(b, "version"),
        icon="mdi:package-variant",
    ),
    PanelSensor(
        key="fw_version",
        name="Firmware version",
        value=lambda b: _info(b, "fwVersion"),
        icon="mdi:chip",
    ),
    # Also set as the device registry's serial_number (bridge._register_device),
    # which renders in the device info card. Duplicated as an entity because
    # that card is easy to miss and every other identity field is already here —
    # and only an entity can go on a dashboard or into a template.
    PanelSensor(
        key="serial",
        name="Serial number",
        value=lambda b: _info(b, "serial"),
        icon="mdi:identifier",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bridge = entry.runtime_data
    entities: list[SensorEntity] = [DomoIlluminanceSensor(bridge), DomoLicenseSensor(bridge)]
    entities += [DomoInfoSensor(bridge, desc) for desc in INFO_SENSORS]
    async_add_entities(entities)


class DomoInfoSensor(DomoPanelEntity, SensorEntity):
    """A sensor whose value comes from the cached ``sys/info`` payload."""

    def __init__(self, bridge: PanelBridge, desc: PanelSensor) -> None:
        super().__init__(bridge)
        self._desc = desc
        self._attr_name = desc.name
        self._attr_unique_id = f"{bridge.device_id}_{desc.key}"
        self._attr_device_class = desc.device_class
        self._attr_state_class = desc.state_class
        self._attr_native_unit_of_measurement = desc.unit
        if desc.icon:
            self._attr_icon = desc.icon
        if desc.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        return self._desc.value(self.bridge)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_info(self.bridge.device_id), self._updated
            )
        )

    @callback
    def _updated(self, _data: dict[str, Any]) -> None:
        self.async_write_ha_state()


class DomoLicenseSensor(DomoPanelEntity, SensorEntity):
    """Licence state as reported by the panel's own verifier (``sys/license``).

    State is one of ``valid`` / ``expired`` / ``serial_mismatch`` /
    ``bad_signature`` / ``missing`` / … — the same vocabulary the signing tool
    prints, so a support answer can be looked up in one place.

    ``days_remaining`` is the attribute worth automating on. Licences expire with
    NO grace period, so a renewal that lands a day late means a watermarked panel;
    an automation on this is the intended way to avoid that.
    """

    _attr_name = "Licence"
    _attr_icon = "mdi:certificate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_license"

    @property
    def native_value(self) -> str | None:
        lic = self.bridge.license
        if not lic:
            return None  # panel has not reported yet
        if lic.get("valid"):
            return "valid"
        return lic.get("reason") or "invalid"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lic = self.bridge.license
        attrs: dict[str, Any] = {
            "features": lic.get("features") or [],
            "serial": lic.get("serial") or self.bridge.sys_info.get("serial"),
        }
        exp = lic.get("exp")
        if isinstance(exp, (int, float)):
            attrs["expires"] = dt_util.utc_from_timestamp(exp).isoformat()
            remaining = exp - dt_util.utcnow().timestamp()
            # Floor at 0 rather than going negative: "expired 3 days ago" is
            # already carried by the state, and a negative number in a threshold
            # automation reads as "plenty of time left".
            attrs["days_remaining"] = max(0, int(remaining // 86400))
        # False means the APK is not the one we signed. Surfaced rather than
        # hidden: it is the difference between "customer needs a licence" and
        # "this panel is running something we did not build".
        if lic.get("integrity") is False:
            attrs["integrity"] = False
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_license(self.bridge.device_id), self._updated
            )
        )

    @callback
    def _updated(self, _data: dict[str, Any]) -> None:
        self.async_write_ha_state()


class DomoIlluminanceSensor(DomoPanelEntity, SensorEntity):
    """Ambient light (raw sensor counts) from ``sys/light``."""

    _attr_name = "Illuminance"
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "lx"

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_illuminance"

    @property
    def native_value(self) -> int | None:
        return self.bridge.light_raw

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_light(self.bridge.device_id), self._updated
            )
        )

    @callback
    def _updated(self, _raw: int) -> None:
        self.async_write_ha_state()
