"""ADB-over-TCP provisioning backend for the config panel's Setup/Update tool.

Speaks the ADB wire protocol straight from Home Assistant via the pure-python
``adb-shell`` library — NO adb *binary* is needed in the HA container (the same
approach the core Android TV integration takes). It exists to bootstrap a
brand-new Sonoff NSPanel Pro *before it is even talking MQTT*: install/update the
app APK and poke the device (wake, back, home, type text, show notifications,
dump memory).

Design notes
------------
* Blocking ``adb-shell`` calls run in the executor; each request opens a fresh
  TCP connection and closes it. Provisioning is occasional — a kept-alive socket
  buys nothing and risks going stale between a phone-in and the next action.
* ``adb-shell`` is imported LAZILY inside the executor helpers, so a missing /
  not-yet-installed dependency degrades to a clear per-action error instead of
  breaking integration load.
* The first connect with our generated key blocks until the user accepts the
  one-time "Allow USB debugging?" prompt ON THE PANEL — surfaced as ``auth``.
* Admin-gated at the WebSocket layer (the sidebar panel is admin-only anyway):
  this can install software on, and type passwords into, a device on the LAN.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ADB_KEY_FILE, APP_PACKAGE, GITHUB_OWNER, GITHUB_REPO

_LOGGER = logging.getLogger(__name__)

_DATA_KEY = "domodreams_panel_adb"

#: On-device staging path for the APK before ``pm install``.
_REMOTE_APK = "/data/local/tmp/domodreams_panel_update.apk"
#: Socket timeout for the install connection. Must exceed the read timeouts
#: below, so a silent curl download doesn't trip the transport layer first.
_XFER_TIMEOUT = 320.0
#: The panel downloads the APK itself with curl (adb-shell's pure-python push
#: hangs on this device's adbd). curl is silent, so adb-shell sees no shell
#: output until it finishes — the read timeout must cover the whole download.
_DL_CURL_TIMEOUT = 240
_DL_READ_TIMEOUT = 300.0
#: Read timeout for ``pm install`` (verifies + optimizes the APK on-device).
_INSTALL_READ_TIMEOUT = 180.0
#: Short socket timeout for tiny shell round-trips.
_OP_TIMEOUT = 20.0
#: How long ``connect`` waits for the user to tap "Allow USB debugging?".
_AUTH_TIMEOUT = 18.0


class AdbError(Exception):
    """An ADB operation failed. ``code`` is a stable slug for the WS client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get_adb(hass: HomeAssistant) -> "PanelAdb":
    """Component-wide :class:`PanelAdb` singleton (created on first use)."""
    adb = hass.data.get(_DATA_KEY)
    if adb is None:
        adb = PanelAdb(hass)
        hass.data[_DATA_KEY] = adb
    return adb


def _parse_version_name(dumpsys_out: str | None) -> str | None:
    """Pull ``versionName=…`` out of ``dumpsys package <pkg>`` output."""
    if not dumpsys_out:
        return None
    for raw in dumpsys_out.splitlines():
        line = raw.strip()
        if line.startswith("versionName="):
            return line.split("=", 1)[1].strip() or None
    return None


def _input_text_arg(text: str) -> str:
    """Quote free text for Android's ``input text`` (one shell arg).

    Single-quote for the shell (only ``'`` is special inside single quotes, so
    ``$``, backticks, ``"`` etc. in a password pass through literally), and map
    spaces to ``%s`` — Android's ``input`` treats a literal space as an arg
    separator, so ``%s`` is the documented way to type one. A literal ``%`` is
    left as-is; passwords rarely contain ``%s``.
    """
    safe = text.replace("'", "'\\''").replace(" ", "%s")
    return f"'{safe}'"


class PanelAdb:
    """Runs ADB-over-TCP actions against a panel from Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._signer: Any = None
        self._signer_lock = asyncio.Lock()

    # --- key / signer -------------------------------------------------------
    async def _get_signer(self) -> Any:
        if self._signer is not None:
            return self._signer
        async with self._signer_lock:
            if self._signer is None:
                self._signer = await self._hass.async_add_executor_job(
                    self._load_or_make_signer
                )
        return self._signer

    def _load_or_make_signer(self) -> Any:
        """Load (or first-time generate) our persisted ADB key → a signer."""
        try:
            from adb_shell.auth.keygen import keygen
            from adb_shell.auth.sign_pythonrsa import PythonRSASigner
        except ImportError as err:  # dependency not installed yet
            raise AdbError(
                "unavailable",
                "The adb-shell library isn't installed yet. Restart Home "
                "Assistant once after updating the integration, then try again.",
            ) from err

        key_path = self._hass.config.path(".storage", ADB_KEY_FILE)
        if not os.path.isfile(key_path):
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            keygen(key_path)  # writes key_path and key_path + ".pub"
        with open(key_path, encoding="utf-8") as fh:
            priv = fh.read()
        with open(key_path + ".pub", encoding="utf-8") as fh:
            pub = fh.read()
        return PythonRSASigner(pub, priv)

    # --- low-level connect/run ---------------------------------------------
    def _run(
        self,
        host: str,
        port: int,
        signer: Any,
        fn: Callable[[Any], Any],
        *,
        xfer: bool = False,
    ) -> Any:
        """Blocking: connect → ``fn(device)`` → close. Runs in the executor.

        Exceptions are classified by type NAME (not by importing adb-shell's
        exception classes), so this keeps working across adb-shell versions that
        rename them.
        """
        try:
            from adb_shell.adb_device import AdbDeviceTcp
        except ImportError as err:
            raise AdbError(
                "unavailable",
                "The adb-shell library isn't installed yet. Restart Home "
                "Assistant once after updating the integration, then try again.",
            ) from err

        timeout = _XFER_TIMEOUT if xfer else _OP_TIMEOUT
        device = AdbDeviceTcp(host, int(port), default_transport_timeout_s=timeout)
        try:
            device.connect(
                rsa_keys=[signer],
                auth_timeout_s=_AUTH_TIMEOUT,
                transport_timeout_s=timeout,
            )
        except Exception as err:  # noqa: BLE001 — classify below, re-raise as AdbError
            name = type(err).__name__
            if "Auth" in name:
                raise AdbError(
                    "auth",
                    "The panel hasn't authorised this Home Assistant yet. Look at "
                    "the panel screen for an “Allow USB debugging?” prompt, "
                    "tap “Always allow”, then try again.",
                ) from err
            raise AdbError(
                "connect",
                f"Couldn't reach ADB at {host}:{port} — is the panel powered "
                f"on, on the network, and is ADB (port {port}) enabled? ({err})",
            ) from err

        try:
            return fn(device)
        finally:
            try:
                device.close()
            except Exception:  # noqa: BLE001 — closing a dead socket is fine
                pass

    # --- public actions -----------------------------------------------------
    async def async_probe(self, host: str, port: int) -> dict[str, Any]:
        """Connect and report model / Android version / installed app version."""
        signer = await self._get_signer()
        return await self._hass.async_add_executor_job(
            self._probe_blk, host, port, signer
        )

    def _probe_blk(self, host: str, port: int, signer: Any) -> dict[str, Any]:
        def fn(device: Any) -> dict[str, Any]:
            model = (device.shell("getprop ro.product.model") or "").strip()
            android = (device.shell("getprop ro.build.version.release") or "").strip()
            serial = (device.shell("getprop ro.serialno") or "").strip()
            app_version = _parse_version_name(
                device.shell(f"dumpsys package {APP_PACKAGE} | grep versionName")
            )
            return {
                "model": model or None,
                "android": android or None,
                "serialno": serial or None,
                "app_version": app_version,
                "app_installed": app_version is not None,
            }

        return self._run(host, port, signer, fn)

    async def async_keyevent(self, host: str, port: int, code: int) -> dict[str, Any]:
        """Inject a key event (26=power, 4=back, 3=home, …)."""
        signer = await self._get_signer()
        await self._hass.async_add_executor_job(
            self._shell_blk, host, port, signer, f"input keyevent {int(code)}"
        )
        return {"code": int(code)}

    async def async_text(self, host: str, port: int, text: str) -> dict[str, Any]:
        """Type ``text`` on the device as if from the on-screen keyboard."""
        if not text:
            raise AdbError("bad_request", "No text to send.")
        signer = await self._get_signer()
        cmd = f"input text {_input_text_arg(text)}"
        await self._hass.async_add_executor_job(
            self._shell_blk, host, port, signer, cmd
        )
        return {"chars": len(text)}

    async def async_expand_notifications(self, host: str, port: int) -> dict[str, Any]:
        """Pull down the notification shade."""
        signer = await self._get_signer()
        await self._hass.async_add_executor_job(
            self._shell_blk, host, port, signer, "cmd statusbar expand-notifications"
        )
        return {}

    async def async_meminfo(self, host: str, port: int) -> dict[str, Any]:
        """Return ``dumpsys meminfo`` (memory usage + running apps)."""
        signer = await self._get_signer()
        out = await self._hass.async_add_executor_job(
            self._meminfo_blk, host, port, signer
        )
        return {"output": out}

    async def async_set_home(self, host: str, port: int) -> dict[str, Any]:
        """Make the app the device's default Home (launcher) activity."""
        signer = await self._get_signer()
        out = await self._hass.async_add_executor_job(
            self._shell_blk,
            host,
            port,
            signer,
            f"cmd package set-home-activity {APP_PACKAGE}/com.domopanel.MainActivity",
        )
        low = (out or "").lower()
        if "success" not in low and any(
            k in low for k in ("error", "exception", "failure", "not found")
        ):
            raise AdbError(
                "set_home_failed",
                "Couldn't set the app as Home — is it installed (and Home-capable)? "
                f"({out.strip() or 'no output'})",
            )
        return {"output": (out or "").strip()}

    async def async_unset_home(self, host: str, port: int) -> dict[str, Any]:
        """Remove the app as default Home — point Home at Android's blank
        ``FallbackHome`` so pressing Home does nothing (an empty screen)."""
        signer = await self._get_signer()
        out = await self._hass.async_add_executor_job(
            self._shell_blk,
            host,
            port,
            signer,
            "cmd package set-home-activity com.android.settings/.FallbackHome",
        )
        low = (out or "").lower()
        if "success" not in low and any(
            k in low for k in ("error", "exception", "failure", "not found")
        ):
            raise AdbError(
                "unset_home_failed",
                f"Couldn't unset the Home app: {out.strip() or 'no output'}",
            )
        return {"output": (out or "").strip()}

    async def async_reboot(self, host: str, port: int) -> dict[str, Any]:
        """Reboot the device. The ADB socket drops as it goes down — that IS the
        success signal, so a read error after the command is sent is expected."""
        signer = await self._get_signer()
        await self._hass.async_add_executor_job(self._reboot_blk, host, port, signer)
        return {}

    def _reboot_blk(self, host: str, port: int, signer: Any) -> None:
        def fn(device: Any) -> None:
            try:
                device.shell(
                    "setprop persist.adb.tcp.port 5555; svc power reboot; reboot",
                    read_timeout_s=4,
                )
            except Exception:  # noqa: BLE001 — socket drops as the device reboots
                pass

        self._run(host, port, signer, fn)

    def _shell_blk(self, host: str, port: int, signer: Any, cmd: str) -> str:
        return self._run(host, port, signer, lambda d: d.shell(cmd) or "")

    def _meminfo_blk(self, host: str, port: int, signer: Any) -> str:
        text = self._run(
            host,
            port,
            signer,
            lambda d: d.shell("dumpsys meminfo", read_timeout_s=20) or "",
        )
        # Cap the payload so a huge dump can't bloat the WS frame.
        return text[:60000]

    # --- install / update ---------------------------------------------------
    async def async_install_latest(
        self, host: str, port: int, progress: Callable[..., None] | None = None
    ) -> dict[str, Any]:
        """Install/update the app by having the PANEL download the release APK.

        adb-shell's pure-python file *push* hangs on this device's adbd, but plain
        shell commands are reliable and the panel has ``curl`` + working TLS to
        GitHub â so the panel fetches the APK itself and ``pm install``s it.
        ``progress(message, level)`` (optional) is called on the event loop with
        short human-readable steps so the GUI can show a live log.
        """
        say = progress or (lambda *a, **k: None)

        say("Finding the latest release on GitHub…")
        url, tag, name = await self._fetch_latest_apk_url()
        say(f"Latest release {tag} — {name}.", "ok")

        signer = await self._get_signer()
        say(f"Panel is downloading {name} from GitHub… (this can take a bit)")
        size = await self._hass.async_add_executor_job(
            self._download_blk, host, port, signer, url
        )
        say(f"Downloaded {size:,} bytes to the panel.", "ok")

        say("Installing on the panel (pm install)…")
        version = await self._hass.async_add_executor_job(
            self._pm_install_blk, host, port, signer
        )
        say(
            f"Installed{(' v' + version) if version else ''} and launched — done.",
            "ok",
        )
        return {"tag": tag, "asset": name, "version": version, "package": APP_PACKAGE}

    async def async_latest_version(self) -> dict[str, Any]:
        """Latest available app version from the GitHub release (no ADB needed)."""
        _url, tag, name = await self._fetch_latest_apk_url()
        version = tag[1:] if tag.startswith("v") else tag
        return {"tag": tag, "version": version or None, "asset": name}

    async def _fetch_latest_apk_url(self) -> tuple[str, str, str]:
        """Return (download_url, tag, asset_name) for the latest release APK."""
        session = async_get_clientsession(self._hass)
        api = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
            "/releases/latest"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "domodreams-panel-integration",
        }
        try:
            async with session.get(
                api, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status in (403, 404):
                    raise AdbError(
                        "no_release",
                        "No public GitHub release found for the app yet. Publish a "
                        f"release with an .apk asset on {GITHUB_OWNER}/{GITHUB_REPO} "
                        "(and make the repo public) first.",
                    )
                if resp.status != 200:
                    raise AdbError(
                        "github",
                        f"GitHub returned HTTP {resp.status} fetching the latest "
                        "release.",
                    )
                data = await resp.json()
        except asyncio.TimeoutError as err:
            raise AdbError(
                "github", "Timed out contacting GitHub for the latest release."
            ) from err
        except aiohttp.ClientError as err:
            raise AdbError("github", f"Couldn't reach GitHub: {err}") from err

        tag = data.get("tag_name") or "latest"
        asset = next(
            (
                a
                for a in (data.get("assets") or [])
                if str(a.get("name", "")).lower().endswith(".apk")
            ),
            None,
        )
        if not asset:
            raise AdbError(
                "no_asset", f"The latest release ({tag}) has no .apk asset attached."
            )
        return (
            str(asset.get("browser_download_url")),
            tag,
            str(asset.get("name") or "app-release.apk"),
        )

    def _download_blk(self, host: str, port: int, signer: Any, url: str) -> int:
        """On the panel: curl the APK to a temp path. Returns the byte size."""
        remote = _REMOTE_APK
        # -L follows the release -> CDN redirect; --fail => nonzero on HTTP error.
        # curl is silent (-sS): nothing is read until it finishes, so the read
        # timeout must cover the whole download (see _DL_READ_TIMEOUT).
        cmd = (
            f"rm -f {remote}; "
            f"curl -L -sS --fail -m {_DL_CURL_TIMEOUT} -o {remote} '{url}' "
            f"&& echo __DL_OK__ $(wc -c < {remote}) "
            f"|| echo __DL_FAIL__ rc=$?"
        )
        out = self._run(
            host,
            port,
            signer,
            lambda d: d.shell(cmd, read_timeout_s=_DL_READ_TIMEOUT) or "",
            xfer=True,
        )
        if "__DL_OK__" not in out:
            raise AdbError(
                "download",
                "The panel couldn't download the APK from GitHub — check its "
                f"internet access. (curl: {out.strip()[:200] or 'no output'})",
            )
        sizes = [int(tok) for tok in out.split() if tok.isdigit()]
        return sizes[-1] if sizes else 0

    def _pm_install_blk(self, host: str, port: int, signer: Any) -> str | None:
        """On the panel: ``pm install`` the downloaded APK, then read its version."""
        remote = _REMOTE_APK

        def fn(device: Any) -> str | None:
            out = (
                device.shell(
                    f"pm install -r -d {remote}", read_timeout_s=_INSTALL_READ_TIMEOUT
                )
                or ""
            )
            try:  # best-effort on-device cleanup
                device.shell(f"rm -f {remote}")
            except Exception:  # noqa: BLE001
                pass
            if "Success" not in out:
                low = out.lower()
                if (
                    "signatures do not match" in low
                    or "update_incompatible" in low
                    or "inconsistent_certificates" in low
                ):
                    raise AdbError(
                        "signature",
                        "A build with a different signature is already installed. "
                        f"Uninstall it first (adb uninstall {APP_PACKAGE}) — that "
                        "wipes its local data — then install again.",
                    )
                raise AdbError(
                    "install_failed",
                    f"pm install failed: {out.strip() or 'unknown error'}",
                )
            version = _parse_version_name(
                device.shell(f"dumpsys package {APP_PACKAGE} | grep versionName")
            )
            try:  # best-effort: launch the app so the panel comes up ready
                device.shell(
                    f"monkey -p {APP_PACKAGE} -c android.intent.category.LAUNCHER 1"
                )
            except Exception:  # noqa: BLE001
                pass
            return version

        return self._run(host, port, signer, fn, xfer=True)
