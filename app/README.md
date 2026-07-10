# TIPS Planner — standalone (Windows / macOS)

Cross-platform standalone rewrite of the Miele-LXIV plugin. Same compute core (`tips_core`),
own DICOM loader (pydicom + GDCM) and own UI (PySide6, Qt). No Miele dependency.

> ⚠️ Research / education / self-training only. Not a medical device. Not intra-procedural
> navigation. The operator makes all final clinical decisions.

The geometry (`tips_core/geometry.py`) is the **single source of truth**, ported verbatim from the
verified Mac plugin (oblique-MPR reslice, side-firing fan, 2-axis deflection, tip-bend, needle arc).

## Run

**macOS**: double-click **`run.command`** (first launch creates the venv + installs deps automatically).

**Manual (macOS)** — the venv must live at a **space-free path** (see warning):
```bash
python3.13 -m venv ~/.tips_planner/venv
~/.tips_planner/venv/bin/pip install -r requirements.txt
~/.tips_planner/venv/bin/python main.py     # run main.py straight from the repo
```
> ⚠️ **Two hard requirements (both confirmed by crashes on this machine):**
> 1. **Use Python 3.13** — PySide6 6.11 aborts on Python 3.14 (Qt platform plugin won't load).
> 2. **The venv (where Qt lives) must NOT be under a path containing spaces.** Qt cannot load its
>    platform plugin through a space-containing path (e.g. `…/My Projects/…`) and aborts at
>    `createPlatformIntegration`. Keep the venv at `~/.tips_planner/venv`; the source code may stay
>    in the spaced repo path. `run.command` does this automatically.

Entry point = the **Database** (Miele-style): **Import DICOM folder…** → studies appear with
thumbnails and an editable **Comment** column → double-click a series to open it in the viewer.

## Features
- **Database entry point** (Miele-style): import DICOM, study/series tree, thumbnails, an editable
  per-study comment, search, open → viewer. The catalog lives in the OS app-data folder, so patient
  data stays local and never enters the repository. An **anonymise** toggle masks patient name and ID
  in the list.
- DICOM series load (including JPEG-Lossless) → HU volume; `.npy` loader for development.
- **4-pane viewer**: axial / coronal / sagittal / synthetic **ICE fan**, with the IVC path, θ rotation,
  A-P and L-R deflection, probe pull-back, window-level drag, ICE flip, and the fan projected back onto
  the CT panes.
- **Needle planning**: Entry → Target with an adjustable arc, drawn on all four panes and in the 3D view.
- Zoom / pan on every pane; state slots; self-update; English and Japanese UI.

## Layout
- `tips_core/geometry.py` — OS-agnostic compute core (numpy only; no scipy).
- `tips_core/liver.py` — liver surface extraction for the 3D pane.
- `dicom_io.py` — DICOM series → `Volume(array, sx, sy, dz)`; `.npy` loader.
- `catalog.py` / `database_view.py` — local study catalog and its UI.
- `main.py` — PySide6 viewer.
- `tests/` — `python -m pytest tests -q`

## License
GPL-3.0-or-later, as for the repository as a whole (see `../LICENSE`). PySide6 is used under the LGPL;
NumPy, pydicom and GDCM are permissive.

*M. Yamamoto.*
