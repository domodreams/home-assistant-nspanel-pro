# DomoDreams NSPanel Pro — promo site

A small, static, dependency-free marketing site. Plain HTML + one shared CSS +
one tiny JS file. No build step needed to serve it — the gallery is the only
generated file (see below).

## Pages

| File | Purpose |
|---|---|
| `index.html` | Homepage — general info, what it is, how it works |
| `features.html` | Full feature tour (page types, tiles, camera, alarm, themes, kiosk) |
| `gallery.html` | Theme gallery — all 32 themes on two grids, switchable dropdown |
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

`gallery.html` embeds all 32 themes as inline WebP data URIs (~1.15 MB), rendered
from the same specimen as the app. It was generated from the reference artifact's
data; the generator script lives in the scratchpad. To rebuild after new theme
renders, re-run the generator against fresh `{theme: {salone, casa, label, tone}}`
data. It is a plain static file — no runtime dependency on the generator.
