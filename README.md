# TIPS ICE Planner

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21328321.svg)](https://doi.org/10.5281/zenodo.21328321)

Pre-procedural planning for **TIPS** (transjugular intrahepatic portosystemic shunt).

From a contrast-enhanced CT, the software **predicts the side-firing ICE (intracardiac
echocardiography) view** that an AcuNav-type transducer would produce from inside the inferior
vena cava, and overlays the **Entry → Target needle trajectory** on four synchronised planes
(axial, coronal, sagittal, and the synthetic ICE fan).

> ⚠️ **Research, education and self-training only.**
> **This is not a medical device. This is not intra-procedural navigation.**
> It does not propose an optimal or recommended puncture route, and it makes no claim of reducing
> the number of punctures, the radiation dose or the procedure time.
> **All clinical decisions are made by the operator.**

---

## What it does

The inferior vena cava is traced on the CT, which gives the catheter's centreline. The transducer
is then modelled along that centreline: tip deflection about two orthogonal axes, a constant-curvature
tip bend, and a 90° side-firing imaging sector. Resampling the CT volume with that geometry
(oblique-MPR reslice followed by an ultrasound scan-conversion) yields the plane the ICE probe would
actually see at a given rotation angle θ, deflection and pull-back position.

The predicted view deliberately keeps **CT appearance** rather than synthesising speckle: the aim is a
geometric rehearsal of *where the portal vein, hepatic vein and IVC will sit in the ICE image*, not a
photorealistic ultrasound simulation.

## Layout

| | |
|---|---|
| `app/` | The application. macOS and Windows. Python 3.13 / PySide6 / pydicom, with its own DICOM loader, catalogue and viewer. |

`app/tips_core/geometry.py` holds the geometry: the oblique-MPR reslice, the side-firing sector, the
two-axis deflection, the tip bend and the needle arc.

## Running the standalone app

### From a release build
Download the build for your platform, unzip, and run it. No Python installation is needed.
Quick-start notes in Japanese: [`docs/quickstart_ja_macOS.md`](docs/quickstart_ja_macOS.md) ·
[`docs/quickstart_ja_Windows.md`](docs/quickstart_ja_Windows.md).

### From source
```bash
python3.13 -m venv ~/.tips_planner/venv
~/.tips_planner/venv/bin/pip install -r app/requirements.txt
~/.tips_planner/venv/bin/python app/main.py
```
On macOS you can simply double-click `app/run.command`, which creates the virtual environment on
first launch.

> **Two hard requirements**, both confirmed by reproducible crashes:
> 1. **Python 3.13.** PySide6 6.11 aborts on Python 3.14 (the Qt platform plugin fails to load).
> 2. **The virtual environment must live at a path without spaces.** Qt cannot load its platform
>    plugin through a space-containing path and aborts in `createPlatformIntegration`. The source
>    tree itself may sit anywhere. `run.command` handles this by placing the venv at
>    `~/.tips_planner/venv`.

### Workflow
1. **Database** — `Import DICOM folder…`, then double-click a series to open it. No CT of your own
   handy? Point it at [`sample_data/HCC048_portal_venous/`](sample_data/HCC048_portal_venous/).
2. **Step 1 (ICE setup)** — click along the IVC on the axial pane to define the probe path, choose the
   access route (femoral or jugular), then explore with θ / probe position / deflection.
3. **Step 2 (Needle)** — place Entry and Target; the needle arc is drawn on all four panes.

Full user manuals: [`app/docs/manual_en.pdf`](app/docs/manual_en.pdf) ·
[`app/docs/manual_ja.pdf`](app/docs/manual_ja.pdf)

## Tests

```bash
~/.tips_planner/venv/bin/python -m pytest app/tests -q
```

## Example data

The software reads any contrast-enhanced abdominal CT series in DICOM. A portal-venous-phase series
is required, because the portal vein, hepatic veins and IVC must all be opacified.

A ready-to-open sample is bundled at [`sample_data/HCC048_portal_venous/`](sample_data/HCC048_portal_venous/)
(89 images, DICOM) so the app can be tried immediately after cloning — no need to source your own CT.
It is drawn from the public **HCC-TACE-Seg** collection of The Cancer Imaging Archive, released under
**CC BY 4.0** (no registration required); full attribution and required citations are in
[`sample_data/ATTRIBUTION.md`](sample_data/ATTRIBUTION.md):

> Moawad AW, Fuentes D, Morshid A, et al. *Multimodality annotated HCC cases with and without
> advanced imaging segmentation* [Data set]. The Cancer Imaging Archive; 2021.
> doi:[10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924)

## Data and privacy

Patient DICOM never leaves the machine. Imported studies, thumbnails and comments are stored in the
operating system's per-user application-data folder. **No private patient data is contained in, or
committed to, this repository** — the only imaging shipped here is the public-domain, CC BY 4.0
sample above, which is not the author's own case material.

## Citation

If this software supports your work, please cite it by its archived DOI. The concept DOI below always
resolves to the latest release; each release also has its own DOI if you need to pin the exact version
you ran.

> Yamamoto M. *TIPS ICE Planner.* Zenodo. doi:[10.5281/zenodo.21328321](https://doi.org/10.5281/zenodo.21328321)

Machine-readable metadata is in [`CITATION.cff`](CITATION.cff).

## License

**GNU General Public License v3.0 or later** — see [`LICENSE`](LICENSE) (identical text is provided as
`Licence.txt`). The standalone application uses PySide6 under the LGPL; NumPy, pydicom and GDCM are
permissive.

## Support

Please open an issue on the GitHub repository.

---
*Masayoshi Yamamoto — interventional radiologist, Department of Radiology, Teikyo University School of Medicine, Tokyo, Japan.*
