"""Shared active-discovery probe.

This is the ONE legitimately cross-panel operation: it publishes on the shared
``domodreams/panel/discover`` topic and collects replies from every online panel
on ``domodreams/panel/+/sys/discovery``, while also picking up retained
``avail`` / ``sys/info`` so panels that are online-but-quiet are still listed.

Used by the config flow (to offer a pick-list) and by the ``discover`` service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .const import (
    RESERVED_DEVICE_ID,
    TOPIC_DISCOVER,
    TOPIC_WILD_AVAIL,
    TOPIC_WILD_DISCOVERY,
    TOPIC_WILD_INFO,
    device_id_from_topic,
)

_LOGGER = logging.getLogger(__name__)


async def async_probe(hass: HomeAssistant, timeout: float = 2.0) -> dict[str, dict[str, Any]]:
    """Discover panels. Returns ``{device_id: info}``.

    ``info`` merges retained ``avail``/``sys/info`` with any active
    ``sys/discovery`` reply: ``{available, model, ip, version}``.
    """
    found: dict[str, dict[str, Any]] = {}

    def _entry(device_id: str) -> dict[str, Any]:
        return found.setdefault(device_id, {})

    @callback
    def on_avail(msg: mqtt.ReceiveMessage) -> None:
        did = device_id_from_topic(msg.topic)
        if did and did != RESERVED_DEVICE_ID:
            _entry(did)["available"] = msg.payload == "online"

    @callback
    def on_info(msg: mqtt.ReceiveMessage) -> None:
        did = device_id_from_topic(msg.topic)
        if not did or did == RESERVED_DEVICE_ID:
            return
        data = _load(msg.payload)
        e = _entry(did)
        for k in ("model", "ip", "version"):
            if data.get(k) is not None:
                e[k] = data[k]

    @callback
    def on_disc(msg: mqtt.ReceiveMessage) -> None:
        data = _load(msg.payload)
        did = data.get("deviceId")
        if not isinstance(did, str) or did == RESERVED_DEVICE_ID:
            return
        e = _entry(did)
        e["available"] = True
        for k in ("model", "ip", "version"):
            if data.get(k) is not None:
                e[k] = data[k]

    unsubs = [
        await mqtt.async_subscribe(hass, TOPIC_WILD_AVAIL, on_avail),
        await mqtt.async_subscribe(hass, TOPIC_WILD_INFO, on_info),
        await mqtt.async_subscribe(hass, TOPIC_WILD_DISCOVERY, on_disc),
    ]
    try:
        # allow retained messages to land, then probe
        await asyncio.sleep(0.3)
        await mqtt.async_publish(
            hass, TOPIC_DISCOVER, json.dumps({"v": 1, "id": uuid.uuid4().hex})
        )
        await asyncio.sleep(timeout)
    finally:
        for unsub in unsubs:
            unsub()
    _LOGGER.debug("discovery probe found: %s", found)
    return found


def _load(payload: str) -> dict[str, Any]:
    try:
        v = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}
