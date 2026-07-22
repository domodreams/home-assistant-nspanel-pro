"""WebSocket API for the config panel SPA (Milestone 7).

Commands (all under the ``domodreams_panel/`` namespace) consumed by the
sidebar panel's frontend over ``hass.connection``:

* ``list``          — configured panels (device_id, name, model, sw_version, online)
* ``get_config``    — panels JSON + device config + ``revision`` for one panel
* ``validate``      — schema + geometry errors for a candidate panels doc ([] = ok)
* ``save_config``   — conflict-check → validate → persist file(s) → publish (ADMIN)
* ``get_icons``     — accepted icon names (kit glyph registry)
* ``get_themes``    — accepted theme names (schema enum)
* ``screenshot``    — ask the panel for a fresh screenshot and RETURN the PNG
* ``remote``        — remote-control a panel: wake / tap / page (debug view)
* ``notify``        — send (or clear) a notification on a panel (ADMIN)

Destructive commands (``save_config``, ``notify``) require an admin connection.
The others are read-only; the sidebar panel itself is already admin-gated.

Optimistic concurrency: ``get_config`` returns a ``revision`` (content hash of
the two on-disk files). ``save_config`` requires that revision back and rejects
with ``conflict: true`` when the disk state changed since the client loaded it,
so a stale tab can never silently overwrite a newer config. ``force: true``
bypasses the check deliberately (still admin-only).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import panel_config
from .adb import AdbError, get_adb
from .const import ADB_DEFAULT_PORT, CONF_DEVICE_ID, DOMAIN, signal_screenshot
from .notify_payload import MAX_NOTIFY_ACTIONS, async_build_notify_payload

_LOGGER = logging.getLogger(__name__)

#: How long to wait for the panel to answer a screenshot request. The panel
#: encodes a 480x480 PNG and pushes it over MQTT (~30-90KB), which is quick; this
#: is generous enough to survive a busy panel but short enough that a dead one
#: fails fast with a clear message instead of hanging the button.
SCREENSHOT_TIMEOUT = 12


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register every WS command (idempotent — safe to call once at setup)."""
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_validate)
    websocket_api.async_register_command(hass, ws_save_config)
    websocket_api.async_register_command(hass, ws_get_icons)
    websocket_api.async_register_command(hass, ws_get_themes)
    websocket_api.async_register_command(hass, ws_screenshot)
    websocket_api.async_register_command(hass, ws_remote)
    websocket_api.async_register_command(hass, ws_notify)
    websocket_api.async_register_command(hass, ws_adb)
    websocket_api.async_register_command(hass, ws_adb_install)
    websocket_api.async_register_command(hass, ws_app_latest)
    _LOGGER.debug("Registered %s websocket commands", DOMAIN)


def _bridge_for(hass: HomeAssistant, device_id: str):
    """Resolve the live :class:`PanelBridge` for a panel deviceId, if loaded."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_DEVICE_ID) == device_id:
            return entry.runtime_data
    return None


@websocket_api.websocket_command({vol.Required("type"): "domodreams_panel/list"})
@callback
def ws_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List configured panels with liveness + firmware info."""
    panels: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        device_id = entry.data.get(CONF_DEVICE_ID)
        if not device_id:
            continue
        bridge = entry.runtime_data
        info: dict[str, Any] = getattr(bridge, "sys_info", {}) or {}
        panels.append(
            {
                "device_id": device_id,
                "name": entry.title or device_id,
                "entry_id": entry.entry_id,
                # Device-registry id: what a service call targets, and what the
                # Notify tab needs to print a copyable example.
                "ha_device_id": getattr(bridge, "ha_device_id", None),
                "model": info.get("model"),
                "sw_version": info.get("version"),
                # Self-reported IP (from the retained sys/info topic) — handy in
                # the panel picker, and to pre-fill the ADB Setup tool.
                "ip": info.get("ip"),
                "online": bool(getattr(bridge, "available", False)),
                "loaded": bridge is not None,
            }
        )
    connection.send_result(msg["id"], {"panels": panels})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/get_config",
        vol.Required("device_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the panels + device config for one panel (seeds on first use).

    Includes the current ``revision`` — the client must echo it to
    ``save_config`` (optimistic concurrency).
    """
    device_id = msg["device_id"]
    try:
        panels = await panel_config.async_load_or_seed(hass, device_id)
        device = await panel_config.async_load_or_seed_device(hass, device_id)
        revision = await panel_config.async_revision(hass, device_id)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "read_failed", str(err))
        return
    connection.send_result(
        msg["id"],
        {
            "device_id": device_id,
            "panels": panels,
            "device": device,
            "revision": revision,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/validate",
        vol.Required("panels"): dict,
    }
)
@callback
def ws_validate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate a candidate panels doc; return the (possibly empty) error list."""
    errors = panel_config.validate_panels(msg["panels"])
    connection.send_result(msg["id"], {"valid": not errors, "errors": errors})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/save_config",
        vol.Required("device_id"): str,
        vol.Required("panels"): dict,
        vol.Required("revision"): str,
        vol.Optional("device"): dict,
        vol.Optional("force"): bool,
    }
)
@websocket_api.async_response
async def ws_save_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Conflict-check → validate → persist → publish.

    Rejects (without writing or publishing anything) when:
    * ``revision`` no longer matches the on-disk state (``conflict: true``) —
      unless ``force: true``;
    * the panels doc fails schema/geometry validation.

    On success the result carries the NEW ``revision`` for the client to adopt.
    """
    device_id = msg["device_id"]
    panels = msg["panels"]

    # 1) freshness — never let a stale tab clobber a newer config
    current_rev = await panel_config.async_revision(hass, device_id)
    if not msg.get("force") and msg["revision"] != current_rev:
        connection.send_result(
            msg["id"],
            {
                "saved": False,
                "conflict": True,
                "revision": current_rev,
                "errors": [
                    "conflict: config changed since you loaded it — reload "
                    "the latest config and re-apply your changes"
                ],
            },
        )
        return

    # 2) validity
    errors = panel_config.validate_panels(panels)
    if errors:
        connection.send_result(msg["id"], {"saved": False, "errors": errors})
        return

    bridge = _bridge_for(hass, device_id)
    if bridge is None:
        connection.send_error(
            msg["id"], "unknown_device", f"No loaded panel for '{device_id}'"
        )
        return

    try:
        await panel_config.async_write_panels(hass, device_id, panels)
        if isinstance(msg.get("device"), dict):
            await panel_config.async_write_device(hass, device_id, msg["device"])
        # Re-reads from disk, re-validates, publishes retained config/*, reloads.
        push_errors = await bridge.async_push_config()
        new_rev = await panel_config.async_revision(hass, device_id)
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "save_failed", str(err))
        return

    if push_errors:
        connection.send_result(msg["id"], {"saved": False, "errors": push_errors})
        return
    connection.send_result(
        msg["id"], {"saved": True, "errors": [], "revision": new_rev}
    )


@websocket_api.websocket_command({vol.Required("type"): "domodreams_panel/get_icons"})
@callback
def ws_get_icons(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    connection.send_result(msg["id"], {"icons": panel_config.icon_names()})


@websocket_api.websocket_command({vol.Required("type"): "domodreams_panel/get_themes"})
@callback
def ws_get_themes(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    connection.send_result(msg["id"], {"themes": panel_config.theme_names()})


#: The panel is 480x480 at mdpi (dp == px), so a tap coordinate is simply a pixel
#: in that square. Anything outside is a bug in the caller, not something to
#: forward to the panel.
PANEL_PX = 480


@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/remote",
        vol.Required("device_id"): str,
        vol.Required("action"): vol.In(("wake", "tap", "page")),
        #: tap
        vol.Optional("x"): vol.All(vol.Coerce(float), vol.Range(min=0, max=PANEL_PX)),
        vol.Optional("y"): vol.All(vol.Coerce(float), vol.Range(min=0, max=PANEL_PX)),
        vol.Optional("ms"): vol.All(vol.Coerce(int), vol.Range(min=20, max=2000)),
        #: page — relative (delta) or absolute (index)
        vol.Optional("delta"): vol.All(vol.Coerce(int), vol.Range(min=-20, max=20)),
        vol.Optional("index"): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
    }
)
@websocket_api.async_response
async def ws_remote(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Drive a panel from the config UI's remote-control view (debug).

    Deliberately a SMALL surface — wake, a single tap, and page navigation. No
    gesture forwarding: the point is to poke the UI from a browser while
    developing, not to reimplement a touchscreen over MQTT.

    A tap is injected on the panel as a real MotionEvent inside the app's own
    window (``cmd/touch`` → RemoteTouchModule), so it behaves exactly like a
    finger — including waking the screensaver's activity timer.
    """
    device_id = msg["device_id"]
    bridge = _bridge_for(hass, device_id)
    if bridge is None:
        connection.send_error(
            msg["id"], "not_found", f"panel '{device_id}' is not loaded"
        )
        return
    if not bridge.available:
        connection.send_error(msg["id"], "offline", f"panel '{device_id}' is offline")
        return

    action = msg["action"]
    if action == "wake":
        await bridge.async_cmd("wake")
    elif action == "tap":
        if "x" not in msg or "y" not in msg:
            connection.send_error(msg["id"], "bad_request", "tap needs x and y")
            return
        payload: dict[str, Any] = {"x": msg["x"], "y": msg["y"]}
        if "ms" in msg:
            payload["ms"] = msg["ms"]
        await bridge.async_cmd("touch", payload)
    else:  # page
        if "delta" in msg:
            await bridge.async_cmd("page", {"delta": msg["delta"]})
        elif "index" in msg:
            await bridge.async_cmd("page", {"index": msg["index"]})
        else:
            connection.send_error(msg["id"], "bad_request", "page needs delta or index")
            return

    connection.send_result(msg["id"], {"sent": action})


def _png_result(png: bytes | None, ts: float | None) -> dict[str, Any]:
    """Inline PNG payload for the SPA (a data: URL drops straight into an <img>)."""
    if not png:
        return {"image": None, "ts": None, "bytes": 0}
    return {
        "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        "ts": ts,
        "bytes": len(png),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/screenshot",
        vol.Required("device_id"): str,
        #: False (default) → return the LAST screenshot we already hold, without
        #: touching the panel. True → ask the panel for a fresh one and wait.
        vol.Optional("refresh", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_screenshot(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the panel's screenshot, cached or freshly captured.

    Two modes on purpose. Opening the config page must NOT wake every panel it
    shows, so the default just hands back the frame the bridge already holds
    (``image: null`` when there is none yet). ``refresh: true`` is the explicit
    user action: it publishes ``cmd/screenshot`` and waits for the panel's reply.

    The integration already has a ``screenshot`` service and a camera entity, but
    neither is usable straight from the SPA: the service is device-targeted (the
    SPA knows our ``device_id``, not HA's device-registry id) and the camera would
    mean resolving an entity_id and juggling an access token. Returning the bytes
    on the websocket the SPA is already authenticated on keeps this to one round
    trip.

    On refresh we wait for a screenshot NEWER than the one we hold
    (``screenshot_ts`` changes), so a stale cached frame can never be passed off
    as a fresh capture.
    """
    device_id = msg["device_id"]
    bridge = _bridge_for(hass, device_id)
    if bridge is None:
        connection.send_error(
            msg["id"], "not_found", f"panel '{device_id}' is not loaded"
        )
        return

    if not msg["refresh"]:
        connection.send_result(
            msg["id"], _png_result(bridge.screenshot_png, bridge.screenshot_ts)
        )
        return

    if not bridge.available:
        connection.send_error(msg["id"], "offline", f"panel '{device_id}' is offline")
        return

    before = bridge.screenshot_ts
    fresh = asyncio.Event()

    @callback
    def _on_shot() -> None:
        if bridge.screenshot_ts != before:
            fresh.set()

    unsub = async_dispatcher_connect(hass, signal_screenshot(device_id), _on_shot)
    try:
        await bridge.async_screenshot()
        try:
            async with asyncio.timeout(SCREENSHOT_TIMEOUT):
                await fresh.wait()
        except TimeoutError:
            connection.send_error(
                msg["id"],
                "timeout",
                f"panel '{device_id}' did not answer within {SCREENSHOT_TIMEOUT}s",
            )
            return
    finally:
        unsub()

    connection.send_result(
        msg["id"], _png_result(bridge.screenshot_png, bridge.screenshot_ts)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/notify",
        vol.Required("device_id"): str,
        #: "send" builds a notification, "clear" takes one (or all) away.
        vol.Required("action"): vol.In(("send", "clear")),
        #: The NOTIFICATION's id. Deliberately not called `id`: in this API that
        #: name belongs to the WebSocket envelope (an int), and a notification
        #: called "washer" would corrupt the frame every reply is keyed by.
        vol.Optional("notify_id"): str,
        vol.Optional("title"): str,
        vol.Optional("message"): str,
        vol.Optional("icon"): str,
        vol.Optional("image"): str,
        vol.Optional("level"): vol.In(("info", "success", "warning", "error")),
        vol.Optional("priority"): vol.In(("normal", "high")),
        vol.Optional("sound"): str,
        vol.Optional("speak"): str,
        vol.Optional("tts_engine"): str,
        vol.Optional("language"): str,
        vol.Optional("timeout"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
        vol.Optional("actions"): vol.All(
            [
                vol.Schema(
                    {
                        vol.Required("id"): str,
                        vol.Optional("label"): str,
                        vol.Optional("icon"): str,
                        # Inline action(s) to run on press — same as the service.
                        vol.Optional("on_press"): object,
                    }
                )
            ],
            vol.Length(max=MAX_NOTIFY_ACTIONS),
        ),
    }
)
@websocket_api.async_response
async def ws_notify(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send a notification to a panel from the config UI's Notify composer.

    Admin-only, like ``save_config``: this puts words on a screen in someone's
    home and can ring its speaker.

    The heavy lifting (rendering ``speak`` to a TTS url, resolving a media-source
    ``sound``) is the SAME code the ``domodreams_panel.notify`` service uses, so
    the composer cannot drift from the service it is a front-end for.
    """
    device_id = msg["device_id"]
    bridge = _bridge_for(hass, device_id)
    if bridge is None:
        connection.send_error(
            msg["id"], "not_found", f"panel '{device_id}' is not loaded"
        )
        return

    if msg["action"] == "clear":
        payload = {"id": msg["notify_id"]} if msg.get("notify_id") else {}
        await bridge.async_cmd("notify_clear", payload)
        connection.send_result(msg["id"], {"sent": "clear"})
        return

    # An offline panel would swallow this silently: `cmd/*` is non-retained, so
    # the notification would simply never exist. Say so instead.
    if not bridge.available:
        connection.send_error(msg["id"], "offline", f"panel '{device_id}' is offline")
        return

    # `notify_id` → `id` on the way into the shared builder, which speaks the
    # service's vocabulary.
    data = {k: v for k, v in msg.items() if k not in ("id", "type", "action", "device_id")}
    if "notify_id" in data:
        data["id"] = data.pop("notify_id")

    try:
        built = await async_build_notify_payload(hass, data)
    except ServiceValidationError as err:
        connection.send_error(msg["id"], "bad_request", str(err))
        return

    payload = built.payload
    if not payload.get("title") and not payload.get("message") and not payload.get("sound"):
        connection.send_error(
            msg["id"], "bad_request", "nothing to show and nothing to play"
        )
        return

    if payload.get("id"):
        bridge.arm_notification_on_press(payload["id"], built.on_press)
    await bridge.async_cmd("notify", payload)
    connection.send_result(msg["id"], {"sent": "notify", "spoken": bool(msg.get("speak"))})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/adb",
        #: What to do over ADB. Deliberately a FIXED verb set — the SPA's Setup
        #: tool, not an arbitrary shell (this can install software and type
        #: passwords onto a device on the LAN).
        vol.Required("action"): vol.In(
            ("probe", "keyevent", "text", "notifications", "meminfo", "set_home")
        ),
        #: The panel's ADB endpoint. This tool runs BEFORE a panel is configured
        #: (no MQTT deviceId yet), so the address is supplied by the operator, not
        #: derived from a config entry.
        vol.Required("host"): str,
        vol.Optional("port", default=ADB_DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        #: keyevent — Android keycode (26=power, 4=back, 3=home).
        vol.Optional("code"): vol.All(vol.Coerce(int), vol.Range(min=0, max=999)),
        #: text — string to type via ``input text``.
        vol.Optional("text"): str,
    }
)
@websocket_api.async_response
async def ws_adb(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Drive a panel over ADB-over-TCP from the config UI's Setup/Update tool.

    Admin-only. Speaks ADB directly from HA (pure-python ``adb-shell``), so the
    first call to a fresh panel raises ``auth`` until the operator taps "Allow
    USB debugging?" on the panel screen. Every failure comes back as a WS error
    with a stable ``code`` (``auth`` / ``connect`` / ``no_release`` / …) so the
    SPA can show a specific hint.
    """
    adb = get_adb(hass)
    action = msg["action"]
    host = msg["host"]
    port = msg["port"]
    try:
        if action == "probe":
            res = await adb.async_probe(host, port)
        elif action == "keyevent":
            if "code" not in msg:
                connection.send_error(msg["id"], "bad_request", "keyevent needs a code")
                return
            res = await adb.async_keyevent(host, port, msg["code"])
        elif action == "text":
            res = await adb.async_text(host, port, msg.get("text", ""))
        elif action == "notifications":
            res = await adb.async_expand_notifications(host, port)
        elif action == "meminfo":
            res = await adb.async_meminfo(host, port)
        else:  # set_home
            res = await adb.async_set_home(host, port)
    except AdbError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    except Exception as err:  # noqa: BLE001 — never leak a raw traceback to the SPA
        _LOGGER.exception("ADB %s failed", action)
        connection.send_error(msg["id"], "adb_error", str(err))
        return

    connection.send_result(msg["id"], {"ok": True, **res})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "domodreams_panel/adb_install",
        vol.Required("host"): str,
        vol.Optional("port", default=ADB_DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
)
@websocket_api.async_response
async def ws_adb_install(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Install/update the app, STREAMING step-by-step progress to the SPA.

    Modelled as a subscription so the SPA can render a LIVE log (installing over
    ADB is slow — download on-device + ``pm install`` — and a silent spinner is a
    bad first-run experience). It confirms the subscription, emits one ``progress``
    event per step, then a final ``done`` (with the result) or ``error`` event.
    """
    mid = msg["id"]
    adb = get_adb(hass)

    @callback
    def emit(message: str, level: str = "info") -> None:
        connection.send_message(
            websocket_api.event_message(
                mid, {"step": "progress", "level": level, "message": message}
            )
        )

    # Register as a subscription (no-op unsub) so the client can unsubscribe
    # cleanly once it sees the terminal event.
    connection.subscriptions[mid] = lambda: None
    connection.send_result(mid)

    try:
        res = await adb.async_install_latest(msg["host"], msg["port"], emit)
        connection.send_message(
            websocket_api.event_message(mid, {"step": "done", "result": res})
        )
    except AdbError as err:
        connection.send_message(
            websocket_api.event_message(
                mid, {"step": "error", "code": err.code, "message": str(err)}
            )
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("ADB install failed")
        connection.send_message(
            websocket_api.event_message(
                mid, {"step": "error", "code": "adb_error", "message": str(err)}
            )
        )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "domodreams_panel/app_latest"})
@websocket_api.async_response
async def ws_app_latest(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Latest available app version (from the GitHub release) — no device needed.

    The Setup tool calls this to tell the operator whether the app installed on
    the panel is already up to date or an update is available.
    """
    try:
        res = await get_adb(hass).async_latest_version()
    except AdbError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "github", str(err))
        return
    connection.send_result(msg["id"], {"ok": True, **res})
