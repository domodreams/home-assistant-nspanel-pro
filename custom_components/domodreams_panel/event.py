"""``event`` entities — one per configured button, plus the alarm.

Event types == the tile's declared ``events`` list. Each entity listens on the
per-device event signal and only reacts to its own button id.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PanelConfigEntry
from .bridge import PanelBridge
from .const import signal_event
from .entity import DomoPanelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bridge = entry.runtime_data
    entities: list[DomoButtonEvent] = []
    seen: set[str] = set()
    for page in bridge.panels.get("pages", []):
        for tile in page.get("tiles", []) or []:
            tid = tile.get("id")
            if not isinstance(tid, str):
                continue

            # Split tiles: ONE event entity per SEGMENT. The
            # event button id stays "<tileId>.<segId>", but the unique_id uses
            # an underscore separator (dot-free — cleaner registry ids; split
            # tile/segment ids are dot-free by validation). Event type: press.
            if tile.get("type") == "split":
                tile_label = tile.get("label") or tid
                for seg in tile.get("buttons") or []:
                    if not isinstance(seg, dict):
                        continue
                    seg_id = seg.get("id")
                    if not isinstance(seg_id, str) or not seg_id:
                        continue
                    button_id = f"{tid}.{seg_id}"
                    if button_id in seen:
                        continue
                    seen.add(button_id)
                    seg_label = seg.get("label") or seg_id
                    entities.append(
                        DomoButtonEvent(
                            bridge,
                            button_id,
                            f"{tile_label} {seg_label}",
                            ["press"],
                            unique_suffix=f"{tid}_{seg_id}",
                        )
                    )
                continue

            events = tile.get("events")
            if not isinstance(events, list) or not events:
                # Switchers (and state buttons) implicitly publish
                # {action:"state", value} even without a declared events list
                # (direct-select) — they still get an entity.
                if tile.get("type") == "switcher" or tile.get("behavior") == "state":
                    events = ["state"]
                else:
                    events = None
            if not events:
                continue
            if tid in seen:
                continue
            seen.add(tid)
            entities.append(
                DomoButtonEvent(bridge, tid, tile.get("label") or tid, list(events))
            )
    # The panel always has an alarm that can fire.
    entities.append(DomoButtonEvent(bridge, "alarm", "Alarm", ["alarm_fired"]))
    async_add_entities(entities)


class DomoButtonEvent(DomoPanelEntity, EventEntity):
    """One panel button surfaced as an HA event entity."""

    def __init__(
        self,
        bridge: PanelBridge,
        button_id: str,
        name: str,
        event_types: list[str],
        *,
        unique_suffix: str | None = None,
    ) -> None:
        super().__init__(bridge)
        # The id carried in the MQTT event (matched in _handle_event). For split
        # segments this is "<tileId>.<segId>".
        self._button_id = button_id
        self._attr_name = name
        self._attr_event_types = event_types
        # unique_suffix lets split segments avoid dots in the registry unique_id
        # while still matching the dotted event button id above.
        self._attr_unique_id = f"{bridge.device_id}_{unique_suffix or button_id}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_event(self.bridge.device_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(
        self, button: str, action: str, value: Any, page: Any, state: Any = None
    ) -> None:
        if button != self._button_id or action not in self.event_types:
            return
        attrs: dict[str, Any] = {}
        if value is not None:
            attrs["value"] = value
        # Split-segment presses carry the visual state at press time —
        # surface it so automations on this entity can key off it.
        if state is not None:
            attrs["state"] = state
        if page is not None:
            attrs["page"] = page
        self._trigger_event(action, attrs)
        self.async_write_ha_state()
