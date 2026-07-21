"""DomoDreams Panel — Home Assistant custom integration.

One config entry == one NSPanel (deviceId). Each entry owns an MQTT
:class:`PanelBridge`, its own entities, and its own panels JSON. Nothing is
shared across panels except the discovery broadcast.
"""

from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .bridge import PanelBridge
from .const import DOMAIN
from .frontend import async_register_frontend
from .padded_audio import async_register_clip_view
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.MEDIA_PLAYER,
    Platform.NOTIFY,
    Platform.SENSOR,
    Platform.SIREN,
]

type PanelConfigEntry = ConfigEntry[PanelBridge]

#: Set up only from config entries — no YAML configuration.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the (component-wide) services, WS API, and config panel once."""
    await async_setup_services(hass)
    websocket_api.async_register(hass)
    async_register_clip_view(hass)  # serves TTS clips padded for the panel
    await async_register_frontend(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PanelConfigEntry) -> bool:
    """Set up one panel from a config entry."""
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT client not available")

    bridge = PanelBridge(hass, entry)
    await bridge.async_setup()
    entry.runtime_data = bridge

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info("Set up panel %s (%s)", entry.title, bridge.device_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PanelConfigEntry) -> bool:
    """Tear down one panel."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.runtime_data is not None:
        await entry.runtime_data.async_unload()
    return unloaded
