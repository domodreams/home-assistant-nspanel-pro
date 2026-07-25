# DomoDreams NSPanel Pro — promo site

A small, static, dependency-free marketing site. Plain HTML + one shared CSS +
one tiny JS file. No build step needed to serve it — the gallery is the only
generated file (see below).

## Pages

| File | Purpose |
|---|---|
| `index.html` | Homepage — general info, what it is, how it works |
| `features.html` | Full feature tour (page types, tiles, camera, alarm, themes, kiosk) |
| `gallery.html` | Style gallery — all 32 styles across a sample six-page layout, switchable dropdown (generated) |
| `setup.html` | Install guide — HACS → add panel → push app → configure |
| `licensing.html` | Pricing + licensing, wired for Stripe checkout |
| `assets/style.css` | Shared design system (light/dark aware) |
| `assets/site.js` | Light/dark toggle, persisted in localStorage |
| `assets/img/*` | Real panel screenshots (copied from `ha/images/`) |

## Design

Lifted from the reference theme-gallery artifact: cool-gray canvas, teal accent,
monospace eyebrow labels, soft shadows, dark device frames. Fully light/dark
aware — respects the OS preference, and the header toggle pins an explicit choice.

## Preview locally

Open `index.html` directly, or serve the folder:

```bash
cd site
python -m http.server 8080
# → http://localhost:8080
```

## Before going live (licensing.html)

The checkout is wired for **Stripe Payment Links** and ships with placeholders:

1. Create a Payment Link per SKU (Full, Grid, Clock, Weather, Alarm, Camera,
   Music) and replace every `https://buy.stripe.com/REPLACE_*` — set both the
   `href` and the `data-plink` attribute (`data-plink` wins at runtime; `href`
   is the no-JS fallback).
2. The serial box appends `?client_reference_id=<serial>` to the link, so the
   panel serial rides along to Stripe and appears on the payment (dashboard /
   webhook). Use it to mint the serial-bound JWT with
   `tools/license-sign/sign.mjs`.
3. On each Payment Link, also add a required custom field **"Panel serial"** as a
   belt-and-braces capture for buyers who skip the box.
4. Prices (`€39` Full / `€9` per page) are **examples** — edit the `.amt` values
   and button labels.

## Regenerating the gallery

`gallery.html` is generated. It shows all 32 styles across a sample six-page
layout (living room, home, clock, security, cameras, music) in the reference
artifact's editorial layout, and swaps every screen at once from the dropdown. The
screenshots are **external** WebP files under `assets/gallery/<style>--<page>.webp`
(192 of them), so the HTML itself stays tiny and the `<img>` src is pure
convention — no image data is inlined.

Both the screenshots and the page come from `tools/showcase-gallery/`:

```bash
# 1. deploy the app with your style changes to the panel first
./scripts/deploy-app.ps1

# 2. capture 192 live screenshots from the panel (6 pages × 32 styles)
bash tools/showcase-gallery/capture.sh          # ~12 min, restores the panel after

# 3. paste a real-looking still into the camera page's black feed area, in every
#    style (the live MJPEG is too flaky to photograph reliably)
python tools/showcase-gallery/overlay-camera.py assets/fake-camera.jpg

# 4. convert to WebP + write site/gallery.html
python tools/showcase-gallery/build-site-gallery.py
```

The camera page's live feed is faked in post: `capture.sh` grabs the themed
chrome with the video area black, and `overlay-camera.py` composites a still
(`tools/showcase-gallery/assets/fake-camera.jpg`) into that rectangle for every
style — a not-yet-loaded MJPEG looks identical to a working one in a screenshot,
so photographing the real feed is unreliable.

The default style, page copy and chips live at the top of `build-site-gallery.py`.
It is a plain static file at runtime — no dependency on the generator once built.
