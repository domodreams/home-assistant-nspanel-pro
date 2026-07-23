"""Config flow — one entry per panel (deviceId).

Three ways in (PLAN.md §3.5, §3.6):

* ``async_step_mqtt`` — HA auto-surfaces any panel it sees publish to
  ``domodreams/panel/+/avail`` (declared in the manifest ``mqtt`` key).
* ``async_step_user`` — active + passive discovery builds a pick-list of live
  panels (deviceId · model · ip · version) with a manual-entry fallback.
* ``async_step_manual`` — free-text deviceId + friendly name.

``discover`` is a reserved deviceId (the shared broadcast topic) and is rejected.

There is no broker field anywhere in this flow. Panels are reached exclusively
through ``homeassistant.components.mqtt``, so the broker is always HA's own —
and HA permits exactly one MQTT config entry, leaving nothing to pick. The flow
therefore SHOWS the broker (the one thing a user setting up a panel needs to
type into the app) and refuses to start when MQTT is not configured at all.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
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


def broker_label(hass: HomeAssistant) -> str | None:
    """``host:port`` of Home Assistant's MQTT broker, or None if not set up.

    There is no broker setting of our own, and deliberately so: this integration
    talks to panels through ``homeassistant.components.mqtt``, so the broker is
    always whatever HA's own MQTT integration points at. Home Assistant allows
    exactly one MQTT config entry, so there is nothing here to choose — only
    something to SHOW, because "which broker will my panel need to reach?" is
    the first question anyone setting up a panel has.
    """
    for entry in hass.config_entries.async_entries(mqtt.DOMAIN):
        host = entry.data.get("broker")
        if not host:
            continue
        port = entry.data.get("port")
        return f"{host}:{port}" if port else str(host)
    return None


def _random_device_id() -> str:
    """A fresh, app-style deviceId (mirrors the app's random 6-char suffix)."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "panel-" + "".join(secrets.choice(alphabet) for _ in range(6))


class DomoDreamsPanelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a single DomoDreams panel."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_id: str | None = None
        self._discovered_info: dict[str, Any] = {}
        self._broker: str = ""

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

        # Every panel reaches Home Assistant over MQTT and nothing else, so
        # without a broker the whole flow is a dead end — including the probe
        # below. Say so plainly instead of showing an empty pick-list.
        broker = broker_label(self.hass)
        if broker is None:
            return self.async_abort(reason="mqtt_required")
        self._broker = broker

        found = await discovery.async_probe(self.hass)
        configured = self._async_current_ids()
        options: dict[str, str] = {}
        for did, info in sorted(found.items()):
            if did in configured or did == RESERVED_DEVICE_ID:
                continue
            options[did] = _label(did, info)

        if not options:
            return await self.async_step_manual()

        options[_MANUAL] = "➕ Set up a new panel (not listed)…"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): vol.In(options)}),
            description_placeholders={"broker": self._broker},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a NEW panel by CHOOSING an id — no live panel required.

        The id you pick here IS the panel's ``instanceName``: set the same value
        in the app (Settings screen) and the two line up. Leave it blank and one
        is generated for you. This step is also how the sidebar config panel
        first appears, so the Setup/Update tool is reachable before a panel is
        even online (chicken-and-egg: you need the tool to bring a panel up, but
        the tool lives behind an entry).
        """
        if not self._broker:
            broker = broker_label(self.hass)
            if broker is None:
                return self.async_abort(reason="mqtt_required")
            self._broker = broker

        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = str(user_input.get(CONF_DEVICE_ID) or "").strip()
            name = str(user_input.get(CONF_NAME) or "").strip()
            if not device_id:
                device_id = _random_device_id()
            if device_id.lower() == RESERVED_DEVICE_ID:
                errors["base"] = "reserved_device"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                return self._create(device_id, name or device_id)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE_ID): str,
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
            description_placeholders={"broker": self._broker},
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
