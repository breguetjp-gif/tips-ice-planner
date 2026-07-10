<!-- Language: English | [日本語](README.ja.md) -->

# TIPS Planner — ICE Puncture Planner (Miele-LXIV plugin)

A research / education plugin for **Miele-LXIV** that lets you rehearse, from a pre-procedural contrast CT, **how the ICE (intracardiac echo) view will look** and **the needle track** for a TIPS (transjugular intrahepatic portosystemic shunt) procedure.

**For**: Miele-LXIV (Mac App Store edition) / macOS (Apple Silicon & Intel)
**Author**: M. Yamamoto (independent developer)
**License**: GPL-3.0 (inherited from Miele-LXIV)

---

## ⚠️ Please read first

This is a **prototype tool for research, education and self-training** only.

- **It is not a certified medical device.**
- It is not intended to diagnose, treat or prevent disease.
- **It is not intraprocedural navigation** (it visualizes the pre-procedural CT for rehearsal).
- **The operator makes all final clinical decisions.**
- The needle track and ICE views are references for the operator to verify and measure — they do not indicate a recommended path, an optimal point, or a reduction in puncture attempts.

---

## What it is

Open a contrast CT (DICOM) in Miele-LXIV, then:

1. **Step 1 — ICE setup**: click on the Axial image to trace the IVC (ICE-probe) path, and choose the **insertion route (Femoral / Jugular)** to fix the probe **tip (TIP)**.
2. **Step 2 — Needle path**: place the **Entry** (puncture) and **Target** points and pick the needle type and curve. The needle track is then shown on the **Axial / Coronal / Sagittal / ICE / 3D** views.

The ICE sector is a side-firing plane emanating from the tip array, and it tracks θ rotation, deflection and probe advance. The **3D linkage** view shows the spatial relationship of the probe, sector and needle, freely rotatable.

---

## Install

### Prerequisite
- **Miele-LXIV (Mac App Store edition)** installed.

### Steps
1. **Launch Miele-LXIV once, then quit** (so it creates its folders).
2. In Finder press **Cmd + Shift + G** and open:
   ```
   ~/Library/Containers/com.bettarini.miele-lxiv/Data/Library/Application Support/miele-lxiv/Plugins/
   ```
3. Drop **`TIPSPlanner.mieleplugin`** into that folder.
4. **Restart Miele-LXIV.**
5. Open a CT and choose menu **Plugins → TIPS Planner**. A research/education notice appears on first run.

> Only if you see a "damaged" error from an unsigned build, run once in Terminal:
> ```
> xattr -dr com.apple.quarantine "~/Library/Containers/com.bettarini.miele-lxiv/Data/Library/Application Support/miele-lxiv/Plugins/TIPSPlanner.mieleplugin"
> ```

---

## Usage

### Step 1: ICE path
- **Click on the Axial image** along the IVC (≥ 2 points). The path is drawn as a cyan line.
- Choose the **insertion route**: `Femoral` / `Jugular`. This fixes the **tip (TIP, blue)** and the deflection direction.
- You can stop here just to review the ICE view.

### Step 2: Needle path
- Pick `Entry`, click the puncture point → it auto-advances to `Target` → click the target. **Green = Entry / Red = Target**.
- `Needle curve` is the only needle control (no needle-type picker): `0°` = straight, range `−20°` to `+30°` (default `+20°`); positive/negative bows the needle to either side. The value is shown.
- **The needle always connects Entry↔Target** and is drawn on all 4 views + ICE (where `×` marks where the needle crosses the ICE plane).
- **Grab the Entry / Target dot on any view and drag** to move it while watching the track on CT.

### View controls
| Action | Effect |
|---|---|
| Left-drag | Window level (horiz = WW, vert = WL) |
| Right-drag / pinch / wheel | Zoom at cursor (ICE: wheel = rotate θ) |
| Two-finger scroll | Scroll slices |
| Right-click | Reset zoom / pan |
| Drag borders | Resize panes |
| θ / Probe / Deflect A-P, L-R | Rotation / advance / tip deflection |
| Rotate 90° / Zero deflection / Flip L/R (ICE) | View rotation / reset deflection / mirror ICE |
| 3D linkage | Drag to rotate (gray = shaft, orange = deflectable tip that bends, blue = array, cyan = sector from the array centre, dashed orange = needle) |

---

## License

- This plugin runs in-process as a derivative of **Miele-LXIV (GPL-3.0)**, so it is distributed under **GPL-3.0**.
- The distribution includes the **full source code** (GPLv3 obligation). See `LICENSE` for the full GPL-3.0 text.

---

## Known limitations

- Visualization is based on the pre-procedural CT; it does not reflect intraprocedural organ deformation, respiration or pulsation.
- The ICE deflection and imaging plane are geometric approximations and do not reproduce real echo appearance (speckle, etc.).
- Needle curvature radii are **provisional** (manufacturers do not publish them; based on the literature, e.g. Zhu 2018).

---

## Support (optional)

If this tool is useful to you, optional **donations (a tip jar)** help sustain development.
(Donations support open-source development — they are not the sale of a medical device.)

- Ko-fi / Buy Me a Coffee: _(coming soon)_
- Donations do not constitute any warranty of function or medical advice.

---

*Made by M. Yamamoto (independent developer).*
