"""The panel as a HA ``notify`` entity — the plain path to its screen.

This is the standard-selector half of notifications: ``notify.send_message``
with a message and an optional title, so the panel shows up wherever HA offers a
notification target, and an automation written for any other device works here
without knowing anything about this integration.

Everything BEYOND message and title — icon, image, severity, sound, spoken text,
action buttons, timeout — lives in the richer ``domodreams_panel.notify``
service (``services.py``). A ``NotifyEntity`` supports exactly TITLE and nothing
else by design, and inventing extra fields here would make the entity lie about
what it accepts.
"""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PanelConfigEntry
from .bridge import PanelBridge
from .entity import DomoPanelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DomoNotifyEntity(entry.runtime_data)])


class DomoNotifyEntity(DomoPanelEntity, NotifyEntity):
    """Send a message to the panel's screen."""

    _attr_name = "Notification"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__(bridge)
        self._attr_unique_id = f"{bridge.device_id}_notify"

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        payload: dict[str, str] = {"message": message}
        if title:
            payload["title"] = title
        await self.bridge.async_cmd("notify", payload)
