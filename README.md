# Phabrillust

**Real digital glitch art, on your desktop.**

Phabrillust is a minimal black-and-white desktop app for glitch art. It does real
**databending**: the image is encoded to PNG or JPEG, the file's bytes are corrupted
(bits flipped, bytes dropped, duplicated or shuffled), and the broken file is decoded
again. The artifacts are genuine decoder errors, not filters made to look like them.
Algorithmic glitch effects (pixel sorting, channel shifts, tears, slices) and noise
effects sit on top of that.

You can paint effects into one part of the image with a selection, stack several passes,
and export at full resolution. Every export is signed with an invisible watermark, so you
can later prove an image came from Phabrillust, even after it has been through social media.

Built with Python and PySide6. Licensed under GPL-3.0.

---

## Highlights

- **Real byte-level databending:** 6 methods on actual PNG / JPEG byte streams. A corrupt
  file never crashes the app: if the bytes won't decode, the step is skipped.
- **15 effects** in three groups (glitch, noise, databend). Each is live, toggleable and tunable.
- **Selections:** rectangle, ellipse or lasso, with invert and feather. Pixels outside the
  selection are left exactly as they were.
- **Passes and history:** *Apply to image* bakes the current effects in. Then you can
  select another area and do a different pass. Undo and Reset work at any time.
- **Reproducible:** one seed drives all the randomness. The same image, settings and seed
  always give the same result, and the seed is recorded in the exported file.
- **Full-resolution export** to PNG or JPEG (quality 10–100). The preview is downscaled
  for speed; export and Apply always run at full size in the background.
- **Signed exports:** an invisible pixel watermark plus readable metadata. The
  **Verify** tab checks any image for both.
- **Generated sources:** start from a blank canvas, a solid color, or a linear or radial gradient.

---

## Install & run

Requires **Python 3.14** and the packages in `requirements.txt` (PySide6, plus NumPy,
Pillow, OpenCV, scikit-image and SciPy).

```bash
python -m pip install -r requirements.txt
```

Only PySide6 is active in `requirements.txt`. The scientific packages are listed as comments
with their tested versions. Uncomment them if your environment doesn't have them yet.

```bash
python main.py                              # start empty
python main.py path/to/image.png            # open an image
python main.py path/to/image.png --seed 42  # with a fixed seed
python -m phabrillust                       # same as main.py
```

---

## How to use it

1. **Get an image.** Click *Open…* or drop a file on the window. Or use *New ▾* for a blank
   canvas, a solid color or a gradient.
2. **Select an area (optional).** Choose Rect, Ellipse or Lasso and drag on the image.
   Hold Shift for a square or circle. The dimmed part stays untouched. *Invert* swaps
   inside and outside, and *Feather* softens the edge. Click without dragging to clear the selection.
3. **Turn on effects.** Tick an effect's title in the right panel, then adjust its sliders.
   Effects that are off collapse to just their title. The preview updates live. If you turn
   *Live preview* off, use *Update* or F5 instead.
4. **Set the seed.** Type a seed or press *Random*. Each seed gives a different variation.
5. **Apply to image** bakes the enabled effects in and turns them off, ready for the next
   pass. Each pass is listed under **History**. **Undo** steps back one pass, and **Reset**
   returns to the original (you can undo that too). Up to 10 steps are kept.
6. **Export** as PNG or JPEG. Effects that are on but not yet applied are included, and so
   is the current selection. The suggested filename is `<name>_phabrillust_<seed>.png`.

### Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Open an image (on the Verify tab: choose images to check) |
| Ctrl+S | Export PNG |
| Ctrl+Shift+S | Export JPEG |
| Ctrl+Enter | Apply to image |
| Ctrl+Z | Undo |
| Ctrl+D | Clear selection |
| Ctrl+Shift+I | Invert selection |
| Esc | Cancel the shape you're drawing |
| F5 | Update preview (when Live preview is off) |
| Ctrl+1 / Ctrl+2 | Switch to the Edit / Verify tab |

---

## Effects

Effects always run in a fixed order: **glitch → noise → databend**. If an effect fails, the
pipeline keeps the previous image, shows a warning and continues.

### Glitch

| Effect | What it does | Controls |
|--------|--------------|----------|
| Channel Shift | Offsets the red, green and blue channels against each other | R/G/B offset, vertical |
| Scanline Tear | Shifts thin horizontal bands sideways | tears, max shift, thickness |
| Pixel Sort | Sorts runs of pixels brighter than a threshold | threshold, direction, sort by lightness / hue / saturation |
| Block Displace | Copies or moves square blocks to random nearby spots | block size, count, max shift, copy / move |
| Glitch Slice | Cuts the image into strips and shifts each one | slices, max shift, direction |

### Noise

| Effect | What it does | Controls |
|--------|--------------|----------|
| Gaussian Noise | Soft random noise | sigma, per channel |
| Salt & Pepper | Scatters pure white and black pixels | amount, salt ratio, per channel |
| Value Noise | Blotchy, multi-scale cloud noise | scale, octaves, persistence, per channel |
| Film Grain | Fine photographic grain with a slight blur | intensity, grain size, monochrome |

### Databend

| Effect | What it does to the bytes |
|--------|---------------------------|
| Databend Bitflip | Flips random bits in the PNG data |
| Databend Drop | Deletes random bytes, so everything after them smears and shifts |
| Databend Duplicate | Repeats random bytes |
| Databend Shuffle | Swaps chunks of the file around |
| Databend PNG IDAT | Corrupts only the compressed pixel data (the IDAT chunks) |
| Databend JPEG Body | Encodes as JPEG, then corrupts the compressed body, which gives blocky color bursts |

The first four have a *Protect headers* option (on by default). It keeps the file's
structure intact, so corrupted files still decode most of the time. When a selection is
active, databending corrupts the bytes of the selected region only.

---

## Signing & verification

Every export is signed in two independent layers.

**1. Metadata.** The inscription
`this image was created using phabrillust by @anti-matrix (seed 42)` is stored as plain text
near the start of the file, so you can read it in Notepad or a hex editor. Exports also carry
PNG text chunks + XMP, or JPEG EXIF + a comment, so Windows *Properties* shows Authors, Title
and Date taken. The metadata never contains GPS data. Most platforms strip metadata on upload.

**2. Invisible pixel watermark.** The text `Done by @Phabrillust` is embedded in the pixels
themselves, with a maximum change of about 7 brightness levels (around 44 dB PSNR).

- **Survives:** JPEG down to ~quality 50, WebP ~75, resizing (tested down to 480 px),
  and repeated re-uploads.
- **Does not survive:** cropping, rotation, mirroring, or brightness/contrast edits.
- Needs exports of roughly **256 px or larger** to be reliable.

**Checking an image.** Open the **Verify** tab and drop one or more images on it (or press
Ctrl+O). Each image gets one of these verdicts:

| Verdict | Meaning |
|---------|---------|
| ● Made with Phabrillust | The watermark was found and reads correctly |
| ~ Signed in metadata only | Only the metadata is present. It is easy to copy, so this is weak evidence |
| ✕ No Phabrillust signature | Nothing found. This doesn't prove the image wasn't made with Phabrillust: it may have been cropped, rotated, edited or made too small |

The tab also shows the watermark confidence, which metadata survived, and the seed if it was
recorded. The Verify tab stays usable while an export is running.

From the command line:

```bash
python -m phabrillust.verify path/to/image.jpg [more images ...]
```

> The watermark key is currently public (it's in the source), so the watermark shows where
> an image came from but is not tamper-proof.

---

## Development

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH=(Get-Location).Path; python tests\verify_spec.py
```

The test script is plain Python (no pytest needed). It prints `[OK] …` for each test and ends
with `ALL VERIFICATION PASSED`.

Project layout:

```
phabrillust/
  engine/    image state & history, effect pipeline, selections, databending,
             source generators, metadata signature, pixel watermark   (no Qt)
  effects/   glitch and noise effects
  gui/       PySide6 interface (the only package that imports Qt)
  verify.py  watermark + metadata check (CLI and Verify tab)
assets/      fonts, icons, Qt stylesheet
tests/       verify_spec.py
```

---

## Credits & license

Made by [@anti-matrix](https://github.com/anti-matrix).

Code is released under the **GNU General Public License v3.0**. See [`LICENSE`](LICENSE).

Bundled fonts, under the SIL Open Font License 1.1:

- **Space Grotesk** (UI): `assets/fonts/SpaceGrotesk-VariableFont_wght.ttf`
- **JetBrains Mono** (numbers and status): `assets/fonts/JetBrainsMono-VariableFont_wght.ttf`

See `assets/fonts/SpaceGrotesk-OFL.txt` and `assets/fonts/JetBrainsMono-OFL.txt`.
