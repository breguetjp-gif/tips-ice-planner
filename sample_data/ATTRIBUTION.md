# Sample CT — source and attribution (CC BY 4.0)

The DICOM series in `HCC048_portal_venous/` is from a **public dataset**, not a real patient of
the author's. It is bundled so the app can be tried immediately after cloning, without needing
your own CT.

---

## Dataset used

| Field | Value |
|---|---|
| Collection | **HCC-TACE-Seg** (The Cancer Imaging Archive, TCIA) |
| License | **CC BY 4.0** (confirmed against the live `LicenseName` field via the TCIA API for all 572 CT series in the collection) |
| Registration / DUA | **Not required** (public collection) |
| Cohort | 105 subjects / CT + segmentation |
| Case used here | `HCC_048`, StudyDate 2001-08-12 |
| Series used here | **SeriesNumber 6, "Recon 3: LIVER 2 PHASE (C/A/P)"** |
| Phase | **Portal-venous** (liver parenchyma 100 HU / aorta 228 HU — see below) |
| Slices / thickness | 89 images / **2.5 mm** slice thickness / 0.703 mm in-plane |
| z range | −396 to −176 mm (220 mm craniocaudal) |
| SeriesInstanceUID | `1.3.6.1.4.1.14519.5.2.1.1706.8374.…` (see the DICOM headers in `HCC048_portal_venous/`) |

### How the phase was determined

Measured on the same case, same cross-section (z = −250 mm):

| Series | Liver parenchyma, mean HU | Aorta, 95th percentile HU | Phase |
|---|---|---|---|
| SN5 "Recon 2" | 68.8 | 377 | arterial |
| **SN6 "Recon 3"** | **99.8** | **228** | **portal-venous ← used here** |

> The SeriesDescription "2 PHASE" / "(C/A/P)" names the acquisition protocol and coverage, not
> the phase itself. In this collection, of the paired Recon 2 / Recon 3 series, the
> higher-numbered one is the portal-venous phase.

---

## Required attribution (per CC BY 4.0)

### Short form (figure captions, slides)
> CT images from the HCC-TACE-Seg collection, The Cancer Imaging Archive (Moawad et al., 2021),
> used under CC BY 4.0. https://doi.org/10.7937/TCIA.5FNA-0924

### Citations (the three TCIA asks be cited together)

1. Moawad AW, Fuentes D, Morshid A, Khalaf AM, Elmohr MM, Abusaif A, Hazle JD, Kaseb AO, Hassan M, Mahvash A, Szklaruk J, Qayyom A, Elsayes K. **Multimodality annotated HCC cases with and without advanced imaging segmentation** [Data set]. The Cancer Imaging Archive; 2021. doi:10.7937/TCIA.5FNA-0924
2. Morshid A, Elsayes KM, Khalaf AM, Elmohr MM, Yu J, Kaseb AO, Hassan M, Mahvash A, Wang Z, Hazle JD, Fuentes D. **A machine learning model to predict hepatocellular carcinoma response to transcatheter arterial chemoembolization.** Radiol Artif Intell. 2019;1(5):e180021. doi:10.1148/ryai.2019180021 (PMID 31858078)
3. Clark K, Vendt B, Smith K, Freymann J, Kirby J, Koppel P, Moore S, Phillips S, Maffitt D, Pringle M, Tarbox L, Prior F. **The Cancer Imaging Archive (TCIA): maintaining and operating a public information repository.** J Digit Imaging. 2013;26(6):1045-1057. doi:10.1007/s10278-013-9622-7 (PMID 23884657)

---

## How to reproduce this download

TCIA NBIA REST API (no authentication required):

```bash
BASE=https://services.cancerimagingarchive.net/nbia-api/services/v1
curl "$BASE/getSeries?Collection=HCC-TACE-Seg&Modality=CT&format=json"
curl "$BASE/getImage?SeriesInstanceUID=<UID>" -o series.zip
```
