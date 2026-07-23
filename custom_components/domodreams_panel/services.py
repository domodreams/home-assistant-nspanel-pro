"""Device-targeted services + the (broadcast) discover service.

All panel commands take an HA ``device`` target — there is never an implicit
"the panel" (PLAN.md §3.5). ``discover`` is the exception: it is a broadcast
probe that isn't tied to a device.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import discovery
from .const import (
    DOMAIN,
    EVENT_DISCOVERY,
    SERVICE_DISCOVER,
    SERVICE_NOTIFY,
    SERVICE_NOTIFY_CLEAR,
    SERVICE_PAGE,
    SERVICE_PING,
    SERVICE_REFRESH_INFO,
    SERVICE_PLAY_MEDIA,
    SERVICE_PUSH_CONFIG,
    SERVICE_SCREENSHOT,
    SERVICE_SET_ALARM,
    SERVICE_SET_SCREEN,
    SERVICE_STOP_MEDIA,
    SERVICE_WAKE,
)
from .notify_payload import (
    MAX_NOTIFY_ACTIONS,
    action as notify_action,
    async_build_notify_payload,
)

if TYPE_CHECKING:
    from .bridge import PanelBridge

_LOGGER = logging.getLogger(__name__)

_DEVICE_ONLY = vol.Schema({}, extra=vol.ALLOW_EXTRA)


def _bridges(hass: HomeAssistant, call: ServiceCall) -> list["PanelBridge"]:
    """Resolve the target device(s) to this integration's bridges."""
    device_ids = call.data.get(ATTR_DEVICE_ID) or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    reg = dr.async_get(hass)
    bridges: list[PanelBridge] = []
    for dev_id in device_ids:
        device = reg.async_get(dev_id)
        if device is None:
            continue
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN and entry.runtime_data is not None:
                bridges.append(entry.runtime_data)
    if not bridges:
        raise ServiceValidationError(
            "No DomoDreams panel matched the target device(s)"
        )
    return bridges


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register services once for the whole integration (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_WAKE):
        return

    async def push_config(call: ServiceCall) -> None:
        for bridge in _bridges(hass, call):
            errors = await bridge.async_push_config()
            if errors:
                raise HomeAssistantError(
                    f"Panel config for {bridge.device_id} is invalid: "
                    + "; ".join(errors)
                )

    async def wake(call: ServiceCall) -> None:
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("wake")

    async def set_screen(call: ServiceCall) -> None:
        payload: dict[str, Any] = {"action": call.data["action"]}
        if "brightness" in call.data:
            payload["brightness"] = call.data["brightness"]
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("screen", payload)

    async def page(call: ServiceCall) -> None:
        payload: dict[str, Any] = {}
        if "index" in call.data:
            payload["index"] = call.data["index"]
        if "id" in call.data:
            payload["id"] = call.data["id"]
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("page", payload)

    async def ping(call: ServiceCall) -> None:
        for bridge in _bridges(hass, call):
            await bridge.async_ping()  # RTT fired on domodreams_panel_pong bus event

    async def refresh_info(call: ServiceCall) -> None:
        # The panel republishes sys/info on its own schedule (config/device
        # sysInfoIntervalS, 60s by default). This is the on-demand path for a
        # dashboard or automation that wants a reading NOW.
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("info")

    async def screenshot(call: ServiceCall) -> None:
        for bridge in _bridges(hass, call):
            await bridge.async_screenshot()

    async def set_alarm(call: ServiceCall) -> None:
        payload = {
            "enabled": call.data["enabled"],
            "hour": call.data["hour"],
            "minute": call.data["minute"],
        }
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("set_alarm", payload)

    async def play_media(call: ServiceCall) -> None:
        payload: dict[str, Any] = {}
        for k in ("url", "asset", "volume"):
            if k in call.data:
                payload[k] = call.data[k]
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("play_media", payload)

    async def stop_media(call: ServiceCall) -> None:
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("stop_media")

    async def notify(call: ServiceCall) -> None:
        # Same builder the config panel's composer uses, so the two front-ends
        # cannot drift (notify_payload.py).
        built = await async_build_notify_payload(hass, call.data)
        for bridge in _bridges(hass, call):
            # Arm BEFORE publishing: on a fast panel the press can come back
            # before the next line would have run.
            if built.payload.get("id"):
                bridge.arm_notification_on_press(built.payload["id"], built.on_press)
            await bridge.async_cmd("notify", built.payload)

    async def notify_clear(call: ServiceCall) -> None:
        payload = {"id": call.data["id"]} if "id" in call.data else {}
        for bridge in _bridges(hass, call):
            await bridge.async_cmd("notify_clear", payload)

    async def discover(call: ServiceCall) -> ServiceResponse:
        timeout = float(call.data.get("timeout", 2.0))
        found = await discovery.async_probe(hass, timeout=timeout)
        for device_id, info in found.items():
            _LOGGER.info("discover: %s %s", device_id, info)
            hass.bus.async_fire(
                EVENT_DISCOVERY, {"device_id": device_id, **info}
            )
        return {"panels": [{"device_id": d, **i} for d, i in found.items()]}

    hass.services.async_register(DOMAIN, SERVICE_PUSH_CONFIG, push_config, _DEVICE_ONLY)
    hass.services.async_register(DOMAIN, SERVICE_WAKE, wake, _DEVICE_ONLY)
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCREEN,
        set_screen,
        vol.Schema(
            {
                vol.Required("action"): vol.In(["on", "off", "dim"]),
                vol.Optional("brightness"): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=1)
                ),
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PAGE,
        page,
        vol.Schema(
            {
                vol.Optional("index"): vol.Coerce(int),
                vol.Optional("id"): cv.string,
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(DOMAIN, SERVICE_PING, ping, _DEVICE_ONLY)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_INFO, refresh_info, _DEVICE_ONLY)
    hass.services.async_register(DOMAIN, SERVICE_SCREENSHOT, screenshot, _DEVICE_ONLY)
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ALARM,
        set_alarm,
        vol.Schema(
            {
                vol.Required("enabled"): cv.boolean,
                vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required("minute"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=59)
                ),
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_MEDIA,
        play_media,
        vol.Schema(
            {
                vol.Optional("url"): cv.string,
                vol.Optional("asset"): cv.string,
                vol.Optional("volume"): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=1)
                ),
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(DOMAIN, SERVICE_STOP_MEDIA, stop_media, _DEVICE_ONLY)
    hass.services.async_register(
        DOMAIN,
        SERVICE_NOTIFY,
        notify,
        vol.Schema(
            {
                vol.Optional("id"): cv.string,
                vol.Optional("title"): cv.string,
                vol.Optional("message"): cv.string,
                vol.Optional("icon"): cv.string,
                vol.Optional("image"): cv.string,
                vol.Optional("level"): vol.In(["info", "success", "warning", "error"]),
                vol.Optional("priority"): vol.In(["normal", "high"]),
                vol.Optional("sound"): cv.string,
                vol.Optional("speak"): cv.string,
                vol.Optional("tts_engine"): cv.string,
                vol.Optional("language"): cv.string,
                vol.Optional("actions"): vol.All(
                    cv.ensure_list, [notify_action], vol.Length(max=MAX_NOTIFY_ACTIONS)
                ),
                # 0 = stays until answered. The panel defaults to 60s.
                vol.Optional("timeout"): vol.All(vol.Coerce(int), vol.Range(min=0)),
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_NOTIFY_CLEAR,
        notify_clear,
        vol.Schema({vol.Optional("id"): cv.string}, extra=vol.ALLOW_EXTRA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISCOVER,
        discover,
        vol.Schema({vol.Optional("timeout"): vol.Coerce(float)}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.debug("Registered %s services", DOMAIN)
