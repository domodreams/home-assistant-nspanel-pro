"""Config flow — one entry per panel (deviceId).

Three ways in:

* ``async_step_mqtt`` — HA auto-surfaces any panel it sees publish to
  ``domodreams/panel/+/avail`` (declared in the manifest ``mqtt`` key).
* ``async_step_user`` — active + passive discovery builds a pick-list of live
  panels (deviceId · model · ip · version) with a manual-entry fallback.
* ``async_step_manual`` — free-text deviceId + friendly name.

``discover`` is a reserved deviceId (the shared broadcast topic) and is rejected.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

from . import discovery
from .const import (
    CONF_DEVICE_ID,
    CONF_NAME,
    DOMAIN,
    RESERVED_DEVICE_ID,
    device_id_from_topic,
)

_LOGGER = logging.getLogger(__name__)

_MANUAL = "__manual__"


class DomoDreamsPanelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a single DomoDreams panel."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_id: str | None = None
        self._discovered_info: dict[str, Any] = {}

    # --- MQTT auto-discovery -------------------------------------------------

    async def async_step_mqtt(
        self, discovery_info: MqttServiceInfo
    ) -> ConfigFlowResult:
        """Triggered when a panel publishes to ``.../+/avail``."""
        device_id = device_id_from_topic(discovery_info.topic)
        if not device_id or device_id == RESERVED_DEVICE_ID:
            return self.async_abort(reason="invalid_device")

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()

        self._discovered_id = device_id
        self.context["title_placeholders"] = {"name": device_id}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered panel."""
        assert self._discovered_id is not None
        if user_input is not None:
            return self._create(self._discovered_id, self._discovered_id)
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"device_id": self._discovered_id},
        )

    # --- user-initiated ------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover live panels and present a pick-list."""
        if user_input is not None:
            choice = user_input[CONF_DEVICE_ID]
            if choice == _MANUAL:
                return await self.async_step_manual()
            return await self._select(choice)

        found = await discovery.async_probe(self.hass)
        configured = self._async_current_ids()
        options: dict[str, str] = {}
        for did, info in sorted(found.items()):
            if did in configured or did == RESERVED_DEVICE_ID:
                continue
            options[did] = _label(did, info)

        if not options:
            return await self.async_step_manual()

        options[_MANUAL] = "Enter a device ID manually…"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): vol.In(options)}),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Free-text deviceId (+ friendly name)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = str(user_input[CONF_DEVICE_ID]).strip()
            name = str(user_input.get(CONF_NAME) or device_id).strip()
            if not device_id:
                errors["base"] = "empty_device"
            elif device_id == RESERVED_DEVICE_ID:
                errors["base"] = "reserved_device"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                return self._create(device_id, name)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): str,
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    async def _select(self, device_id: str) -> ConfigFlowResult:
        if device_id == RESERVED_DEVICE_ID:
            return self.async_abort(reason="invalid_device")
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        return self._create(device_id, device_id)

    def _create(self, device_id: str, name: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=name,
            data={CONF_DEVICE_ID: device_id, CONF_NAME: name},
        )


def _label(device_id: str, info: dict[str, Any]) -> str:
    bits: list[str] = [device_id]
    detail = []
    if info.get("model"):
        detail.append(str(info["model"]))
    if info.get("ip"):
        detail.append(str(info["ip"]))
    if info.get("version"):
        detail.append(f"v{info['version']}")
    if info.get("available") is False:
        detail.append("offline")
    if detail:
        bits.append("(" + ", ".join(detail) + ")")
    return " ".join(bits)
