"""Brand + protocol constants for the DomoDreams Panel integration.

DERIVED from the repo-root ``brand.config.json`` (the single source of truth).
Never hardcode "domodreams" or a topic string anywhere
else in the integration; build topics through the helpers here (mirrors the app
side's ``src/brand.ts``). ``tests``/CI assert this stays in sync with
``brand.config.json``.
"""

from __future__ import annotations

# --- brand (mirror of brand.config.json) ------------------------------------
DOMAIN = "domodreams_panel"
INTEGRATION_NAME = "DomoDreams Panel"
MANUFACTURER = "DomoDreams"

#: MQTT topic prefix: ``{TOPIC_PREFIX}/{deviceId}/...``
TOPIC_PREFIX = "domodreams/panel"

#: Reserved deviceId — the SHARED discovery broadcast topic lives at
#: ``{TOPIC_PREFIX}/discover`` (NOT under a deviceId), so "discover" can never
#: be a real panel identity. Rejected in the config flow.
RESERVED_DEVICE_ID = "discover"

# --- config entry data keys --------------------------------------------------
CONF_DEVICE_ID = "device_id"
CONF_NAME = "name"

# --- HA bus events -----------------------------------------------------------
#: Fired for EVERY panel event (regardless of whether a binding handled it).
EVENT_BUS = "domodreams_panel_event"
#: Fired for each active-discovery reply.
EVENT_DISCOVERY = "domodreams_panel_discovery"
#: Fired for each ping reply, carrying round-trip time.
EVENT_PONG = "domodreams_panel_pong"
#: Fired when a notification is shown on a panel and again when it ends —
#: ``{device_id, id, action: shown|dismissed|expired|action, action_id?}``.
EVENT_NOTIFICATION = "domodreams_panel_notification"

# --- service names -----------------------------------------------------------
SERVICE_PUSH_CONFIG = "push_config"
SERVICE_WAKE = "wake"
SERVICE_SET_SCREEN = "set_screen"
SERVICE_PAGE = "page"
SERVICE_PING = "ping"
SERVICE_SCREENSHOT = "screenshot"
SERVICE_SET_ALARM = "set_alarm"
SERVICE_PLAY_MEDIA = "play_media"
SERVICE_STOP_MEDIA = "stop_media"
SERVICE_NOTIFY = "notify"
SERVICE_NOTIFY_CLEAR = "notify_clear"
SERVICE_DISCOVER = "discover"

# --- storage -----------------------------------------------------------------
#: Per-device panels JSON: ``/config/domodreams_panel/{device_id}.json``.
STORAGE_SUBDIR = DOMAIN


def base_topic(device_id: str) -> str:
    """``domodreams/panel/{device_id}``."""
    return f"{TOPIC_PREFIX}/{device_id}"


def topic_avail(device_id: str) -> str:
    return f"{base_topic(device_id)}/avail"


def topic_event(device_id: str) -> str:
    return f"{base_topic(device_id)}/event"


def topic_config_panels(device_id: str) -> str:
    return f"{base_topic(device_id)}/config/panels"


def topic_config_device(device_id: str) -> str:
    return f"{base_topic(device_id)}/config/device"


def topic_state(device_id: str, key: str) -> str:
    return f"{base_topic(device_id)}/state/{key}"


def topic_cmd(device_id: str, name: str) -> str:
    return f"{base_topic(device_id)}/cmd/{name}"


def topic_sys(device_id: str, name: str) -> str:
    return f"{base_topic(device_id)}/sys/{name}"


#: SHARED (cross-panel) discovery broadcast — the ONE topic not scoped to a
#: deviceId. HA→panels ``{v:1, id}``; each online panel replies on its own
#: ``{deviceId}/sys/discovery``.
TOPIC_DISCOVER = f"{TOPIC_PREFIX}/discover"

#: Wildcards used ONLY for cross-panel discovery (config flow + discover
#: service). Never used to route per-device events.
TOPIC_WILD_AVAIL = f"{TOPIC_PREFIX}/+/avail"
TOPIC_WILD_INFO = f"{TOPIC_PREFIX}/+/sys/info"
TOPIC_WILD_DISCOVERY = f"{TOPIC_PREFIX}/+/sys/discovery"


def device_id_from_topic(topic: str) -> str | None:
    """Extract the deviceId from ``domodreams/panel/{deviceId}/...``."""
    prefix = f"{TOPIC_PREFIX}/"
    if not topic.startswith(prefix):
        return None
    rest = topic[len(prefix):]
    return rest.split("/", 1)[0] or None


def slugify_entity_id(entity_id: str) -> str:
    """Default ``stateKey`` for an entity (mirrors app ``slugifyEntityId``):
    ``light.living_main`` → ``light_living_main``. Lowercase, non-alphanumerics
    collapse to a single underscore.
    """
    out: list[str] = []
    prev_us = False
    for ch in entity_id.lower():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_")


# --- camera pages / PTZ ------------------------------------------------------
#: The four pan/tilt directions a camera page can bind, in D-pad order.
PTZ_DIRECTIONS = ("up", "down", "left", "right")

#: Shorthand PTZ targets are a bare entity_id; the service to "fire" one is
#: derived from its domain. Anything not listed here must use the explicit
#: binding-object form (see ``$defs/ptzTarget`` in panels.schema.json).
PTZ_SERVICE_BY_DOMAIN = {
    "button": "button.press",
    "input_button": "input_button.press",
    "switch": "switch.turn_on",
    "script": "script.turn_on",
    "scene": "scene.turn_on",
}


def ptz_button_id(page_id: str, entity_id: str, direction: str) -> str:
    """Event ``button`` id for one PTZ arrow: ``{pageId}.ptz.{slug}.{dir}``.

    A camera page has no tiles, so its arrows are not real tiles — the bridge
    registers a synthetic tile under this id and the app publishes an ``event``
    with it, which routes through the ordinary binding executor.

    This grammar is a PROTOCOL contract: the app builds the same string in
    ``app/src/config/cameraPage.ts`` (``ptzButtonId``). If the two ever drift,
    PTZ silently stops working — both sides are pinned by tests.

    The ``.ptz.`` infix cannot collide with a split-tile event id
    (``"<tileId>.<segId>"``): validation forbids ``.`` in split tile/segment ids.
    """
    return f"{page_id}.ptz.{slugify_entity_id(entity_id)}.{direction}"


# --- dispatcher signals (per device) ----------------------------------------
def signal_event(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_event"


def signal_awake(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_awake"


def signal_info(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_info"


def signal_light(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_light"


def signal_motion(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_motion"


def signal_media(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_media"


def signal_notification(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_notification"


def signal_screenshot(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_screenshot"


def signal_avail(device_id: str) -> str:
    return f"{DOMAIN}_{device_id}_avail"
