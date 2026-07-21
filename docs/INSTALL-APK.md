# Sideloading the DomoDreams Panel app onto a Sonoff NSPanel Pro

The NSPanel Pro runs Android 8.1 but has **no Play Store**, so the app is
installed by **sideloading** the APK over **ADB** (Android Debug Bridge) across
your network. This takes about five minutes.

> The Home Assistant integration is installed separately, via HACS — see the
> main [README](../README.md). Install the integration **first**.

---

## What you need

- Your **NSPanel Pro**, powered on and on the **same network** as your computer.
- The panel's **IP address**. Find it in your router's client list, or — if you
  already added the panel in Home Assistant — on its device page.
- **ADB** (`adb`) on your computer:
  - **Windows / macOS / Linux:** install the [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools)
    and make sure `adb` is on your `PATH`.
- The **`app-release.apk`** from this repo's
  **[Releases](https://github.com/domodreams/home-assistant-nspanel-pro/releases)** page.

---

## Step 1 — Enable ADB on the panel

The NSPanel Pro's stock firmware exposes **ADB over the network on port 5555**.
On most firmware versions it is reachable out of the box, so you can jump
straight to Step 2 and try to connect.

If the connection is refused, ADB is disabled on your firmware and you need to
turn on Android **Developer options → USB/Network debugging** from the panel's
system settings. The exact menu path varies between firmware revisions — this is
the one step that differs by device — so consult the NSPanel Pro community docs
for your firmware if the toggle isn't where you expect.

## Step 2 — Connect over the network

```bash
adb connect <panel-ip>:5555
```

You should see `connected to <panel-ip>:5555`. Confirm the panel is listed:

```bash
adb devices
# List of devices attached
# <panel-ip>:5555   device
```

If it says `unauthorized`, look at the panel's screen for an **"Allow USB
debugging?"** prompt and accept it (tick *Always allow* to skip it next time).

## Step 3 — Install the APK

```bash
adb install -r app-release.apk
```

`-r` reinstalls/updates in place if a previous build is present. On success you'll
see `Success`.

> **Upgrading from an incompatible build?** If `install` fails with a
> `SIGNATURES DO NOT MATCH` / `INSTALL_FAILED_UPDATE_INCOMPATIBLE` error, an older
> build with a different signature is installed. Remove it first, then reinstall:
> ```bash
> adb uninstall it.domodreams.nspanel
> adb install app-release.apk
> ```
> (This wipes that app's local settings on the device.)

## Step 4 — Launch it

Tap **DomoDreams Panel** on the device, or start it from your computer:

```bash
adb shell monkey -p it.domodreams.nspanel -c android.intent.category.LAUNCHER 1
```

The app runs immersive-fullscreen, keeps the screen awake, and locks to portrait.

## Step 5 — Make it the panel's home screen (kiosk)

For a true wall-panel experience you want DomoDreams Panel to **launch on boot**
and act as the launcher:

1. On the panel, open **Settings → Apps** (or **Home settings**).
2. Set the default **Home app** to **DomoDreams Panel**.

From then on the app starts automatically whenever the panel powers up.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `adb connect` → *connection refused* | ADB isn't enabled — see Step 1. Also confirm the IP and that the panel is on the same subnet. |
| `adb devices` shows `unauthorized` | Accept the **Allow USB debugging** prompt on the panel's screen. |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | Old build with a different signature installed — `adb uninstall it.domodreams.nspanel` first (see Step 3). |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | You downloaded the wrong build. Use the `app-release.apk` from Releases (built `arm64-v8a` for the NSPanel Pro). |
| Panel connects but shows nothing | Check the Home Assistant integration is installed and MQTT is configured — see the main [README](../README.md). |
| The panel's IP keeps changing | Give it a **DHCP reservation** in your router so ADB and MQTT stay reachable. |

Still stuck? Open an issue:
<https://github.com/domodreams/home-assistant-nspanel-pro/issues>.
