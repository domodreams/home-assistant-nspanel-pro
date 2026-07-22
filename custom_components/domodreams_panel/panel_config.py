"""Per-device panels config: load / seed / validate / persist.

The panels JSON for a panel lives at ``/config/domodreams_panel/{device_id}.json``
and the device config at ``/config/domodreams_panel/{device_id}.device.json``.
On first setup they are seeded from the bundled reference examples. Validation
uses ``panels.schema.json`` bundled next to this module — that file is the
AUTHORITATIVE schema (it is the only copy loaded at runtime and the only copy
``deploy-ha.ps1`` ships; ``tools/theme-import`` patches its theme enum in place).

All file IO is pushed to the executor so nothing blocks the event loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from homeassistant.core import HomeAssistant

from .const import PTZ_SERVICE_BY_DOMAIN, STORAGE_SUBDIR

_LOGGER = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_SCHEMA_PATH = _MODULE_DIR / "panels.schema.json"
_SEED_PANELS = _MODULE_DIR / "reference" / "reference-panel.json"
_SEED_DEVICE = _MODULE_DIR / "reference" / "reference-device.json"
_ICONS_PATH = _MODULE_DIR / "reference" / "icons.json"


def _storage_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(STORAGE_SUBDIR))


def panels_path(hass: HomeAssistant, device_id: str) -> Path:
    return _storage_dir(hass) / f"{device_id}.json"


def device_path(hass: HomeAssistant, device_id: str) -> Path:
    return _storage_dir(hass) / f"{device_id}.device.json"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    return Draft202012Validator(schema)


def validate_panels(doc: Any) -> list[str]:
    """Return a list of human-readable schema violations ([] == valid)."""
    errors: list[str] = []
    for err in sorted(_validator().iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{loc}: {err.message}")
    errors.extend(_semantic_errors(doc))
    return errors


def _camera_page_errors(pi: int, page: dict[str, Any]) -> list[str]:
    """Camera-page checks the JSON Schema can't express: a camera may only be
    listed once per page (two selector entries for the same entity is always an
    authoring mistake), and a PTZ shorthand must name an entity in a domain we
    know how to fire — otherwise the press would fail silently at call time.
    """
    errors: list[str] = []
    cfg = page.get("camera")
    if not isinstance(cfg, dict):
        return errors  # the schema already required it
    loc = f"pages/{pi}"
    seen: list[str] = []
    for ci, cam in enumerate(cfg.get("cameras") or []):
        if not isinstance(cam, dict):
            continue
        entity = cam.get("entity")
        if isinstance(entity, str) and entity:
            if entity in seen:
                errors.append(
                    f"{loc}/camera/cameras/{ci}: camera '{entity}' is listed "
                    f"more than once on this page"
                )
            seen.append(entity)
        ptz = cam.get("ptz")
        if not isinstance(ptz, dict):
            continue
        for direction, target in ptz.items():
            # Only the shorthand (string) form is checkable here; the object /
            # list form is a full binding and the executor validates it.
            if not isinstance(target, str):
                continue
            domain = target.split(".", 1)[0] if "." in target else ""
            if domain not in PTZ_SERVICE_BY_DOMAIN:
                errors.append(
                    f"{loc}/camera/cameras/{ci}: ptz '{direction}' target "
                    f"'{target}' is not an entity this integration can fire "
                    f"(expected one of {sorted(PTZ_SERVICE_BY_DOMAIN)}). Use the "
                    f"explicit action form for anything else, e.g. "
                    f'{{"action":"call","service":"onvif.ptz",...}}'
                )
    return errors


def _semantic_errors(doc: Any) -> list[str]:
    """Checks the JSON Schema can't express.

    Grid pages: tiles must stay in bounds and must not overlap (mirrors the
    app's ``validateButtonGrid``). Camera pages: see ``_camera_page_errors``.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return errors
    pages = doc.get("pages")
    if not isinstance(pages, list):
        return errors
    for pi, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        if page.get("type") == "camera":
            errors.extend(_camera_page_errors(pi, page))
            continue
        if page.get("type") != "grid":
            continue
        grid = page.get("grid") or {}
        cols = grid.get("columns")
        rows = grid.get("rows")
        tiles = page.get("tiles")
        if not isinstance(cols, int) or not isinstance(rows, int):
            continue
        if not isinstance(tiles, list):
            continue
        loc = f"pages/{pi}"
        occupied: dict[tuple[int, int], str] = {}
        for ti, tile in enumerate(tiles):
            if not isinstance(tile, dict):
                continue
            col = tile.get("col")
            row = tile.get("row")
            if not isinstance(col, int) or not isinstance(row, int):
                continue
            cspan = tile.get("colSpan") if isinstance(tile.get("colSpan"), int) else 1
            rspan = tile.get("rowSpan") if isinstance(tile.get("rowSpan"), int) else 1
            tid = tile.get("id") or f"#{ti}"
            # switcher items must have unique values (schema prose; JSON Schema
            # can't express uniqueness of a sub-property)
            if tile.get("type") == "switcher" and isinstance(tile.get("items"), list):
                values = [
                    it.get("value")
                    for it in tile["items"]
                    if isinstance(it, dict) and it.get("value")
                ]
                dupes = {v for v in values if values.count(v) > 1}
                if dupes:
                    errors.append(
                        f"{loc}/tiles/{ti}: switcher '{tid}' has duplicate item "
                        f"value(s): {sorted(dupes)}"
                    )
            # split segments: unique ids and no dots (the event button id is
            # "<tileId>.<segId>", so a dot in either would make routing ambiguous;
            # JSON Schema can't express these).
            if tile.get("type") == "split" and isinstance(tile.get("buttons"), list):
                if isinstance(tid, str) and "." in tid:
                    errors.append(
                        f"{loc}/tiles/{ti}: split tile id '{tid}' must not contain "
                        f"a '.' (used as the segment separator)"
                    )
                seg_ids = [
                    seg.get("id")
                    for seg in tile["buttons"]
                    if isinstance(seg, dict) and seg.get("id")
                ]
                for sid in seg_ids:
                    if "." in sid:
                        errors.append(
                            f"{loc}/tiles/{ti}: split '{tid}' segment id '{sid}' "
                            f"must not contain a '.'"
                        )
                seg_dupes = {v for v in seg_ids if seg_ids.count(v) > 1}
                if seg_dupes:
                    errors.append(
                        f"{loc}/tiles/{ti}: split '{tid}' has duplicate segment "
                        f"id(s): {sorted(seg_dupes)}"
                    )
            if col + cspan - 1 > cols or row + rspan - 1 > rows:
                errors.append(
                    f"{loc}/tiles/{ti}: tile '{tid}' ({col},{row} span "
                    f"{cspan}x{rspan}) exceeds grid {cols}x{rows}"
                )
                continue
            for c in range(col, col + cspan):
                for r in range(row, row + rspan):
                    prev = occupied.get((c, r))
                    if prev is not None:
                        errors.append(
                            f"{loc}/tiles/{ti}: tile '{tid}' overlaps tile "
                            f"'{prev}' at cell ({c},{r})"
                        )
                    else:
                        occupied[(c, r)] = tid
    return errors


@lru_cache(maxsize=1)
def icon_names() -> list[str]:
    """Accepted icon names (bundled reference/icons.json, generated from the
    rn-neo-kit glyph registry by ha/tools/gen-icons.mjs)."""
    try:
        with _ICONS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        names = data.get("icons")
        return list(names) if isinstance(names, list) else []
    except (OSError, ValueError):
        return []


@lru_cache(maxsize=1)
def theme_names() -> list[str]:
    """Theme names — the ``theme`` enum in the bundled schema (kept in sync with
    the kit THEMES registry by tools/theme-import)."""
    try:
        with _SCHEMA_PATH.open(encoding="utf-8") as fh:
            schema = json.load(fh)
        enum = schema.get("properties", {}).get("theme", {}).get("enum")
        return list(enum) if isinstance(enum, list) else []
    except (OSError, ValueError):
        return []


# --- blocking helpers (run in executor) -------------------------------------


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _seed_and_read(target: Path, seed: Path) -> Any:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
        _LOGGER.info("Seeded %s from bundled reference", target)
    with target.open(encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


# --- async API ---------------------------------------------------------------


async def async_load_or_seed(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    """Load the panels JSON, seeding the file from the reference on first run."""
    return await hass.async_add_executor_job(
        _seed_and_read, panels_path(hass, device_id), _SEED_PANELS
    )


async def async_load_or_seed_device(
    hass: HomeAssistant, device_id: str
) -> dict[str, Any]:
    """Load the device config JSON, seeding it on first run."""
    return await hass.async_add_executor_job(
        _seed_and_read, device_path(hass, device_id), _SEED_DEVICE
    )


async def async_read_panels(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    return await hass.async_add_executor_job(_read_json, panels_path(hass, device_id))


async def async_read_device(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    return await hass.async_add_executor_job(_read_json, device_path(hass, device_id))


async def async_write_panels(
    hass: HomeAssistant, device_id: str, doc: dict[str, Any]
) -> None:
    """Persist the panels JSON (does NOT publish — caller pushes afterwards)."""
    await hass.async_add_executor_job(_write_json, panels_path(hass, device_id), doc)


async def async_write_device(
    hass: HomeAssistant, device_id: str, doc: dict[str, Any]
) -> None:
    """Persist the device config JSON."""
    await hass.async_add_executor_job(_write_json, device_path(hass, device_id), doc)


def _delete_files(panels: Path, device: Path) -> list[str]:
    deleted: list[str] = []
    for p in (panels, device):
        try:
            p.unlink()
            deleted.append(p.name)
        except FileNotFoundError:
            pass
    return deleted


async def async_delete(hass: HomeAssistant, device_id: str) -> list[str]:
    """Remove a panel's on-disk config files. Returns the names deleted."""
    return await hass.async_add_executor_job(
        _delete_files, panels_path(hass, device_id), device_path(hass, device_id)
    )


# --- revisions (optimistic concurrency for the config panel) ------------------
#
# A revision identifies the exact on-disk state of BOTH config files. Clients
# (the sidebar SPA) get it from ``get_config`` and must echo it to
# ``save_config``; a mismatch means someone else changed the config since the
# client loaded it, and the save is rejected instead of silently clobbering the
# newer state. Content-hash based (not mtime) so identical rewrites / touch
# don't invalidate anything.


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def _revision(panels: Path, device: Path) -> str:
    return f"{_file_digest(panels)}.{_file_digest(device)}"


def revision_sync(hass: HomeAssistant, device_id: str) -> str:
    """Blocking revision read (executor only)."""
    return _revision(panels_path(hass, device_id), device_path(hass, device_id))


async def async_revision(hass: HomeAssistant, device_id: str) -> str:
    """Current combined revision of the panels + device files."""
    return await hass.async_add_executor_job(revision_sync, hass, device_id)
