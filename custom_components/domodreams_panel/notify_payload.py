"""Building a ``cmd/notify`` payload — shared by the service and the config UI.

There are two front-ends for notifications (the ``domodreams_panel.notify``
service and the config panel's Notify composer, over the WebSocket API) and
exactly one payload shape. This module is that shape, so the two cannot drift:
the field names, the ``timeout`` → ``timeoutS`` rename, the action shorthand,
and the rule that ``speak`` wins over ``sound`` all live here.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

import voluptuous as vol

from homeassistant.components import media_source
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .media_url import async_resolve_media_url, async_speech_url

_LOGGER = logging.getLogger(__name__)

#: A 480×480 panel fits three finger-sized buttons in a row. The app clamps to
#: the same number; rejecting here means the user finds out at the call site.
MAX_NOTIFY_ACTIONS = 3

#: Fields that travel to the panel unchanged.
_PASSTHROUGH = ("id", "title", "message", "icon", "image", "level", "priority")

ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Optional("label"): cv.string,
        vol.Optional("icon"): cv.string,
        # What to run when this button is pressed. Held HERE, never sent to the
        # panel: the app renders and reports, the integration is what touches
        # Home Assistant.
        vol.Optional("on_press"): object,
    }
)


def action(value: Any) -> dict[str, Any]:
    """One notification button, as an object or as bare text.

    ``actions: [ok, snooze]`` is the shorthand people actually write in YAML;
    it means the same as ``[{id: ok, label: ok}, …]``.
    """
    if isinstance(value, str):
        return {"id": value, "label": value}
    parsed = ACTION_SCHEMA(value)
    parsed.setdefault("label", parsed["id"])
    return parsed


def normalise_on_press(spec: Any) -> list[dict[str, Any]]:
    """What a button should DO, in the panel's binding grammar.

    Two spellings are accepted, and the rule between them is "a dot means a
    service":

    * **Home Assistant's own**, which is what anyone writing YAML already has in
      their fingers and can paste from the docs::

          on_press: { action: cover.open_cover, target: { entity_id: cover.x } }

    * **the panel's binding grammar**, the same one tiles use and the config
      GUI's action builder emits::

          on_press: { action: call, service: cover.open_cover, entity_id: cover.x }

    A list of either runs in order. Everything normalises to the binding form so
    there is one executor, not two.
    """
    if spec is None:
        return []
    items = spec if isinstance(spec, list) else [spec]
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise vol.Invalid(f"on_press: expected an action object, got {item!r}")
        verb = item.get("action")
        if not isinstance(verb, str) or not verb:
            raise vol.Invalid("on_press: every action needs an `action`")
        if "." in verb:
            call: dict[str, Any] = {"action": "call", "service": verb}
            if isinstance(item.get("data"), dict):
                call["data"] = item["data"]
            target = item.get("target")
            if isinstance(target, dict):
                call["target"] = target
            elif item.get("entity_id"):
                call["target"] = {"entity_id": item["entity_id"]}
            out.append(call)
        else:
            out.append(dict(item))
    return out


@dataclass(slots=True)
class NotifyBuild:
    """What the panel gets, and what stays here.

    ``on_press`` never crosses MQTT: the panel would not know what to do with a
    service call, and it must not be able to make one. It is executed by the
    bridge when the panel reports which button was pressed.
    """

    payload: dict[str, Any]
    #: action id → the binding(s) to run when that button is pressed.
    on_press: dict[str, list[dict[str, Any]]]


async def async_build_notify_payload(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> NotifyBuild:
    """Turn service/WS input into the panel's ``cmd/notify`` payload.

    Audio is resolved HERE, at call time, and never cached: HA evicts TTS clips
    from its proxy quickly and a stale url answers 404 — which is exactly what a
    silent panel looks like.
    """
    payload: dict[str, Any] = {k: data[k] for k in _PASSTHROUGH if data.get(k)}

    if data.get("timeout") is not None:
        # The protocol field is `timeoutS`; both front-ends say `timeout`
        # because every other HA thing that takes seconds does.
        payload["timeoutS"] = int(data["timeout"])

    actions = [action(a) for a in (data.get("actions") or [])][:MAX_NOTIFY_ACTIONS]
    on_press: dict[str, list[dict[str, Any]]] = {}
    if actions:
        for a in actions:
            bindings = normalise_on_press(a.pop("on_press", None))
            if bindings:
                on_press[a["id"]] = bindings
        payload["actions"] = actions

    # A press is routed back by notification id, so `on_press` needs one. The
    # panel would happily invent its own id, but HA would not know it and the
    # button would do nothing — mint it here instead.
    if on_press and not payload.get("id"):
        payload["id"] = f"auto-{secrets.token_hex(4)}"

    sound = data.get("sound")
    speak = data.get("speak")
    if speak:
        if sound:
            _LOGGER.warning(
                "notify: both `speak` and `sound` given — the panel has one "
                "speaker, so the spoken text wins"
            )
        payload["sound"] = await async_speech_url(
            hass,
            speak,
            engine=data.get("tts_engine"),
            language=data.get("language"),
        )
    elif sound:
        # A bare name is a sound bundled in the APK (`alarm_beep`); anything
        # from HA's media browser has to be resolved to a signed LAN url.
        payload["sound"] = (
            await async_resolve_media_url(hass, sound)
            if media_source.is_media_source_id(sound) or sound.startswith("/")
            else sound
        )

    return NotifyBuild(payload=payload, on_press=on_press)
