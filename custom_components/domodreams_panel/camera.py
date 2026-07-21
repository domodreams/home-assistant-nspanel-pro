"""Camera entity holding the last panel screenshot (from ``sys/screenshot``).

Request a fresh frame with the ``screenshot`` service; the picture updates when
the panel's reply arrives.
"""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PanelConfigEntry
from .bridge import PanelBridge
from .const import signal_avail, signal_screenshot
from .entity import DomoPanelEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DomoScreenshotCamera(entry.runtime_data)])


class DomoScreenshotCamera(DomoPanelEntity, Camera):
    """Last screenshot captured from the panel."""

    _attr_name = "Screenshot"

    def __init__(self, bridge: PanelBridge) -> None:
        DomoPanelEntity.__init__(self, bridge)
        Camera.__init__(self)
        self.content_type = "image/png"
        self._attr_unique_id = f"{bridge.device_id}_screenshot"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return self.bridge.screenshot_png

    @property
    def available(self) -> bool:
        # image is served from cache, so it stays viewable even if the panel
        # goes offline — only mark unavailable when we have nothing to show.
        return self.bridge.available or self.bridge.screenshot_png is not None

    async def async_added_to_hass(self) -> None:
        # Camera's own async_added_to_hass + our availability wiring.
        await Camera.async_added_to_hass(self)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_avail(self.bridge.device_id), self._avail_updated
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_screenshot(self.bridge.device_id),
                self._shot_updated,
            )
        )

    @callback
    def _avail_updated(self, _available: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _shot_updated(self) -> None:
        self.async_write_ha_state()
