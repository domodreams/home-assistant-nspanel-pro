<h1 align="center">DomoDreams Panel</h1>

<p align="center"><b>Turn a Sonoff NSPanel Pro into a gorgeous, fully-custom Home Assistant control panel.</b><br>
Neumorphic buttons, clock, cover &amp; dimmer controls and an alarm keypad — all driven over MQTT, all configured from a visual editor in the Home Assistant sidebar.</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/domodreams/home-assistant-nspanel-pro/blob/main/LICENSE"><img src="https://img.shields.io/badge/integration-MIT-green.svg" alt="Integration license: MIT"></a>
  <img src="https://img.shields.io/badge/app-free%20%2B%20premium-ff8c00.svg" alt="App: free + premium">
  <img src="https://img.shields.io/badge/Home%20Assistant-integration-03A9F4.svg" alt="Home Assistant">
  <img src="https://img.shields.io/badge/device-Sonoff%20NSPanel%20Pro-1abc9c.svg" alt="NSPanel Pro">
  <img src="https://img.shields.io/badge/transport-MQTT-660066.svg" alt="MQTT">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/grid.png"  width="200" alt="Neumorphic control grid">
  <img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/alarm.png" width="200" alt="Alarm keypad">
  <img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/clock.png" width="200" alt="Clock face">
</p>

<p align="center"><i>Real screenshots, straight off the panel (480&times;480).</i></p>

---

## Why you'll like it

- 🎛️ **Neumorphic UI that actually looks premium** — soft, pre-rendered surfaces tuned for the NSPanel Pro's low-power GPU. No flat "dashboard on a tablet" look.
- 🧩 **One document, both sides** — a single config describes *look* (pages &amp; tiles) and *behavior* (what each tile does). The panel renders it; the integration executes it.
- 🛰️ **Everything over MQTT** — buttons publish events, the integration mirrors entity state back, and the panel lights up to match. Optimistic on tap, reconciled from real state.
- 🖥️ **Edit it visually** — a config editor lives right in the Home Assistant sidebar. Build layouts by clicking, not by hand-editing JSON.
- 🏠 **Multi-panel by design** — one Home Assistant drives *N* panels, each scoped to its own device, entities and topic namespace.
- 🔌 **Local push, zero cloud** — `iot_class: local_push`. Your walls don't phone home.

## What's on the panel

Pages are **typed** and you swipe between them. Mix and match per panel:

<table>
  <tr>
    <td align="center" width="50%"><img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/grid.png" width="260" alt="Button grid"><br><b>Grid</b><br><sub>Neumorphic buttons, split tiles &amp; a scene bar</sub></td>
    <td align="center" width="50%"><img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/covers.png" width="260" alt="Cover controls"><br><b>Covers &amp; dimmers</b><br><sub>Tap upper/lower half, long-press to repeat</sub></td>
  </tr>
  <tr>
    <td align="center" width="50%"><img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/clock.png" width="260" alt="Clock"><br><b>Clock</b><br><sub>Analog or digital, themeable, doubles as a screensaver</sub></td>
    <td align="center" width="50%"><img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/alarm.png" width="260" alt="Alarm keypad"><br><b>Alarm</b><br><sub>Full Alarmo keypad — arm, disarm, status</sub></td>
  </tr>
</table>

The panel dims itself on inactivity and wakes on touch or proximity — it owns its own screensaver, so nothing burns in and nothing stays glaring at 3 a.m.

---

## Pricing

**DomoDreams Panel is free to try.** Download the app, sideload it on your NSPanel Pro, and run the whole thing — every page type, the visual editor, multi-panel, all of it.

| | **Free** | **Premium** |
|---|:---:|:---:|
| Every page type, controls &amp; the visual editor | ✅ | ✅ |
| Multi-panel | ✅ | ✅ |
| Home Assistant integration (this repo) | ✅ | ✅ |
| On-screen watermark | 💧 shown | — removed |
| Priority support &amp; updates | — | ✅ |

The free tier shows a small **DomoDreams watermark** on the panel. A one-time **Premium** unlock removes it. Everything else works the same — Premium is about polish and supporting development, not gating features you need to control your home.

> The **Home Assistant integration** in this repository is and stays **free and open source (MIT)** — it's the bridge that makes the panel talk to Home Assistant. The watermark and Premium unlock live in the **app**.

---

## Install — two parts

DomoDreams Panel has two halves that talk over MQTT:

1. **The Home Assistant integration** (this repo) — install via HACS.
2. **The app** — sideload the APK onto your NSPanel Pro.

Do them in that order.

### Requirements

- A **Sonoff NSPanel Pro** (Android 8.1).
- **Home Assistant** with the **MQTT integration** already configured against an MQTT broker (e.g. Mosquitto). The panel connects to the same broker over WebSocket.

### 1 · Install the integration (HACS)

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=domodreams&repository=home-assistant-nspanel-pro&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and add this repository to HACS."></a>

1. **HACS → ⋮ → Custom repositories** → add
   `https://github.com/domodreams/home-assistant-nspanel-pro` · category **Integration**.
2. Install **DomoDreams Panel**, then **restart Home Assistant**.
3. **Settings → Devices &amp; Services → Add Integration → DomoDreams Panel**, and add one entry per panel.
4. Open **NSPanel Pro** in the Home Assistant sidebar to lay out your pages.

> Don't have HACS yet? Install it first: <https://hacs.xyz>.

### 2 · Install the app on your NSPanel Pro

The app ships as an Android APK from this repo's **[Releases](https://github.com/domodreams/home-assistant-nspanel-pro/releases)**. Because the NSPanel Pro has no Play Store, you install it by **sideloading over ADB**.

**Quick version:**

```bash
# 1. Grab the latest app-release.apk from the Releases page above.

# 2. Connect to the panel over the network (it runs ADB on port 5555).
#    Find the panel's IP in your router, or from Home Assistant.
adb connect <panel-ip>:5555

# 3. Install.
adb install -r app-release.apk
```

Then launch **DomoDreams Panel** on the device (or set it as the Home app so it autostarts on boot).

📖 **Full step-by-step, including how to enable ADB and set kiosk/autostart, is in [`docs/INSTALL-APK.md`](https://github.com/domodreams/home-assistant-nspanel-pro/blob/main/docs/INSTALL-APK.md).**

---

## Configure

Everything is edited from the **NSPanel Pro** sidebar panel (admin-only) — pick entities, arrange tiles, choose page types, set the theme. Per-panel device settings (theme, sizes, brightness) can also be tweaked **on the device itself**:

<p align="center"><img src="https://raw.githubusercontent.com/domodreams/home-assistant-nspanel-pro/main/images/settings.png" width="300" alt="On-device settings"></p>

The layout schema ships with the integration at [`custom_components/domodreams_panel/panels.schema.json`](https://github.com/domodreams/home-assistant-nspanel-pro/blob/main/custom_components/domodreams_panel/panels.schema.json) and is validated on both sides, so a panel and its config can never silently disagree. Starter examples live in [`config-examples/`](https://github.com/domodreams/home-assistant-nspanel-pro/tree/main/config-examples/).

## How it works

```
NSPanel Pro (app)  ──MQTT──►  domodreams/panel/{device}/event      (button pressed)
                   ◄─MQTT───   domodreams/panel/{device}/config     (layout + bindings, retained)
                   ◄─MQTT───   domodreams/panel/{device}/state/*    (entity state mirror, retained)
                   ◄─MQTT───   domodreams/panel/{device}/cmd/*      (wake, page, reload, …)
```

- The integration owns the Home Assistant side end-to-end: it creates **event entities**, executes **service-call bindings**, and mirrors entity **state** back to the panel. The app never touches HA discovery topics.
- The panel boots from **retained topics + a local cache**, so if the broker or Home Assistant is down it still shows your last-known UI.
- Reconnects use exponential backoff with jitter; state is always reconciled from `state/*`.

## License &amp; product

- The **Home Assistant integration** in this repository is released under the **MIT License** — see [LICENSE](https://github.com/domodreams/home-assistant-nspanel-pro/blob/main/LICENSE).
- The companion **DomoDreams Panel** app is a separate product: **free to use with an on-screen watermark**, with a one-time **Premium** unlock to remove it.

---

<p align="center"><sub>Made with ♥ by <b>DomoDreams</b> · for the Sonoff NSPanel Pro · not affiliated with Sonoff/ITEAD or Home Assistant.</sub></p>
