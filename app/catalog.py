"""DICOM 目録（Miele Database 相当の入口）。

設計（Miele を参考）:
  - スタディ(患者) → シリーズ の2階層。表＝スタディ、展開でシリーズ。
  - 取込(add_folder)は **メタデータのみ** をスキャン（stop_before_pixels）＝数千件でも高速。
  - サムネイルは **遅延生成＋キャッシュ**（表示要求時に中央スライスをデコード→縮小→.npy保存）。
  - 目録・サムネは PHI を含むため **アプリ専用ローカルフォルダ** に保存（リポジトリには出さない）。
"""
from __future__ import annotations
import os
import glob
import json
import numpy as np

import dicom_io

try:
    import pydicom
except Exception:                                   # pragma: no cover
    pydicom = None


def app_data_dir():
    """OS別のアプリデータ。PySide6があればQStandardPaths、無ければ ~/.tips_planner。"""
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        if not base:
            raise RuntimeError
        d = os.path.join(base, "TIPSPlanner")
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".tips_planner")
    os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
    return d


def _age(ds):
    pa = str(getattr(ds, "PatientAge", "") or "")
    if pa and pa[:3].isdigit():
        return f"{int(pa[:3])} y"
    bd = str(getattr(ds, "PatientBirthDate", "") or "")
    sd = str(getattr(ds, "StudyDate", "") or "")
    if len(bd) == 8 and len(sd) == 8:
        y = (int(sd[:4]) - int(bd[:4])) - (1 if sd[4:8] < bd[4:8] else 0)
        if 0 < y < 150:
            return f"{y} y"
    return ""


def _fmt_date(d, t=""):
    if len(d) == 8:
        s = f"{d[:4]}/{d[4:6]}/{d[6:8]}"
        if len(t) >= 4:
            s += f" {t[:2]}:{t[2:4]}"
        return s
    return d


class Catalog:
    def __init__(self):
        self.dir = app_data_dir()
        self.index_path = os.path.join(self.dir, "index.json")
        self.comments_path = os.path.join(self.dir, "comments.json")
        self.sessions_path = os.path.join(self.dir, "sessions.json")
        self.thumbs = os.path.join(self.dir, "thumbs")
        self.series = []                            # list[dict]
        self.comments = {}                          # study_uid -> コメント文字列（重要: 教育症例マーキング）
        self.sessions = {}                          # study_uid -> {"1"/"2"/"3": 作業状態dict}
        self.load()

    # ---- 永続化 ----
    def load(self):
        if os.path.exists(self.index_path):
            try:
                self.series = json.load(open(self.index_path, encoding="utf-8"))
            except Exception:
                self.series = []
        if os.path.exists(self.comments_path):
            try:
                self.comments = json.load(open(self.comments_path, encoding="utf-8"))
            except Exception:
                self.comments = {}
        if os.path.exists(self.sessions_path):
            try:
                self.sessions = json.load(open(self.sessions_path, encoding="utf-8"))
            except Exception:
                self.sessions = {}

    def save(self):
        json.dump(self.series, open(self.index_path, "w", encoding="utf-8"), ensure_ascii=False)
        self.save_comments()

    def save_comments(self):
        json.dump(self.comments, open(self.comments_path, "w", encoding="utf-8"), ensure_ascii=False)

    def set_comment(self, study_uid, text):
        text = (text or "").strip()
        if text:
            self.comments[study_uid] = text
        else:
            self.comments.pop(study_uid, None)
        self.save_comments()

    # ---- 作業状態の保存（患者ごとにスロット1/2/3）----
    def save_sessions(self):
        json.dump(self.sessions, open(self.sessions_path, "w", encoding="utf-8"), ensure_ascii=False)

    def set_session(self, study_uid, slot, data):
        self.sessions.setdefault(study_uid, {})[str(slot)] = data
        self.save_sessions()

    def get_session(self, study_uid, slot):
        return self.sessions.get(study_uid, {}).get(str(slot))

    def has_session(self, study_uid, slot):
        return str(slot) in self.sessions.get(study_uid, {})

    def clear_session(self, study_uid, slot):
        """患者ごとの保存スロットを1つ削除（元に戻せない）。"""
        if study_uid in self.sessions:
            self.sessions[study_uid].pop(str(slot), None)
            if not self.sessions[study_uid]:
                del self.sessions[study_uid]
            self.save_sessions()

    def _has(self, uid):
        return any(s["series_uid"] == uid for s in self.series)

    # ---- 取込（メタデータのみ）----
    def _scan_groups(self, root, progress=None):
        """DICOMをスキャンしてシリーズ単位にグルーピング（読み取り専用・登録も保存もしない）。
        add_folder / scan_series の共通ロジック。返り値は series_uid をキーにした dict。"""
        paths = [p for p in glob.glob(os.path.join(root, "**", "*"), recursive=True)
                 if os.path.isfile(p) and dicom_io.is_dicom(p)]
        groups = {}                                 # series_uid -> dict
        for i, p in enumerate(paths):
            if progress and i % 50 == 0:
                progress(i, len(paths))
            try:
                ds = pydicom.dcmread(p, stop_before_pixels=True)
            except Exception:
                continue
            if "Rows" not in ds:
                continue
            uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
            if not uid:
                continue
            g = groups.get(uid)
            if g is None:
                g = groups[uid] = dict(
                    series_uid=uid,
                    study_uid=str(getattr(ds, "StudyInstanceUID", "") or ""),
                    patient_name=str(getattr(ds, "PatientName", "") or ""),
                    patient_id=str(getattr(ds, "PatientID", "") or ""),
                    birth=_fmt_date(str(getattr(ds, "PatientBirthDate", "") or "")),
                    age=_age(ds),
                    modality=str(getattr(ds, "Modality", "") or ""),
                    institution=str(getattr(ds, "InstitutionName", "") or ""),
                    study_date=_fmt_date(str(getattr(ds, "StudyDate", "") or ""),
                                         str(getattr(ds, "StudyTime", "") or "")),
                    study_desc=str(getattr(ds, "StudyDescription", "")
                                   or getattr(ds, "BodyPartExamined", "") or ""),
                    series_desc=str(getattr(ds, "SeriesDescription", "") or ""),
                    series_no=int(getattr(ds, "SeriesNumber", 0) or 0),
                    files=[], _keys=[])
            g["files"].append(p)
            g["_keys"].append(dicom_io.slice_sort_key(ds))
        for g in groups.values():
            order = np.argsort(np.array(g["_keys"], float))
            g["files"] = [g["files"][i] for i in order]
            del g["_keys"]
        return groups

    def scan_series(self, root, progress=None):
        """フォルダ内DICOMをシリーズ単位で列挙するだけ（取込前のプレビュー用・登録/保存はしない）。
        返り値: list[dict]（各要素は series_uid/study_uid/patient_name/patient_id/modality/
        series_desc/series_no/files 等を含む）。既に取り込み済みのシリーズも含めて全件返す
        （選択ダイアログ側で「取込済み」を示せるように）。"""
        if pydicom is None:
            raise RuntimeError("pydicom 未導入")
        groups = self._scan_groups(root, progress=progress)
        return list(groups.values())

    def _register_groups(self, groups):
        """スキャン済みグループ(list[dict])を、未取込のものだけカタログへ登録して保存する。"""
        added = 0
        for g in groups:
            uid = g["series_uid"]
            if self._has(uid):
                continue
            files = g["files"]
            rec = {k: g[k] for k in g if k != "files"}
            rec["n"] = len(files)
            rec["files"] = files
            rec["rep"] = files[len(files) // 2]      # サムネ用代表ファイル（中央スライス）
            rec["thumb"] = ""                        # 遅延生成
            self.series.append(rec)
            added += 1
        self.save()
        return added

    def add_folder(self, root, progress=None):
        """フォルダ内の全DICOMシリーズを、元の場所を参照したまま登録する（従来どおりの挙動・コピーなし）。"""
        if pydicom is None:
            raise RuntimeError("pydicom 未導入")
        groups = self._scan_groups(root, progress=progress)
        return self._register_groups(list(groups.values()))

    def import_groups(self, groups, anonymize=False, progress=None):
        """取込ダイアログで選ばれたシリーズだけを登録する。
        anonymize=False（既定）: 元ファイルの場所を参照したまま登録＝ディスク消費なし（add_folderと同じ）。
        anonymize=True: 元ファイルは一切書き換えず、患者情報を書き換えた**別コピー**をアプリ専用領域
        （app_data_dir/studies/anon_<uuid>）へ作成してから、そのコピーを登録する。"""
        if not anonymize:
            return self._register_groups(groups)
        if pydicom is None:
            raise RuntimeError("pydicom 未導入")
        import shutil
        import uuid as _uuid
        store = os.path.join(self.dir, "studies"); os.makedirs(store, exist_ok=True)
        total = sum(len(g["files"]) for g in groups); done = 0
        anon_groups = []
        for g in groups:
            dst = os.path.join(store, "anon_" + _uuid.uuid4().hex[:12]); os.makedirs(dst, exist_ok=True)
            new_files = []
            for i, p in enumerate(g["files"]):
                if progress and done % 20 == 0:
                    progress(done, max(total, 1))
                done += 1
                try:
                    ds = pydicom.dcmread(p)                  # 匿名化は画素を含め再保存が必要（全体を読む）
                    ds.PatientName = "ANONYMOUS"; ds.PatientID = "ANON"
                    for tag in ("PatientBirthDate", "PatientAddress", "PatientTelephoneNumbers",
                               "OtherPatientIDs", "OtherPatientNames"):
                        if tag in ds:
                            setattr(ds, tag, "")
                    out = os.path.join(dst, f"{i:06d}.dcm"); ds.save_as(out)
                    new_files.append(out)
                except Exception:
                    continue
            if not new_files:                                # 全ファイル失敗→空フォルダを掃除して読み飛ばす
                shutil.rmtree(dst, ignore_errors=True); continue
            g2 = dict(g); g2["files"] = new_files
            g2["patient_name"] = "ANONYMOUS"; g2["patient_id"] = "ANON"; g2["birth"] = ""; g2["age"] = ""
            anon_groups.append(g2)
        return self._register_groups(anon_groups)

    # ---- スタディ単位に集約（表示用）----
    def studies(self):
        by = {}
        for s in self.series:
            by.setdefault(s["study_uid"], []).append(s)
        out = []
        for uid, ss in by.items():
            ss.sort(key=lambda x: x.get("series_no", 0))
            head = ss[0]
            out.append(dict(
                study_uid=uid, patient_name=head["patient_name"], patient_id=head["patient_id"],
                birth=head["birth"], age=head["age"], institution=head["institution"],
                study_date=head["study_date"], study_desc=head["study_desc"],
                modality=head["modality"], n_images=sum(x["n"] for x in ss),
                n_series=len(ss), series=ss, comment=self.comments.get(uid, "")))
        out.sort(key=lambda d: d["study_date"], reverse=True)
        return out

    # ---- サムネイル（遅延生成＋キャッシュ）----
    def thumbnail(self, series, maxdim=120):
        """series dict → (uint8 [h,w]) 縮小グレー。失敗時 None。"""
        uid = series["series_uid"]
        cache = os.path.join(self.thumbs, uid.replace(".", "_")[:120] + ".npy")
        if os.path.exists(cache):
            try:
                return np.load(cache)
            except Exception:
                pass
        if pydicom is None:
            return None
        try:
            ds = pydicom.dcmread(series["rep"])
            arr = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
            inter = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
            arr = arr * slope + inter
            wc = getattr(ds, "WindowCenter", None); wwd = getattr(ds, "WindowWidth", None)
            try:
                wl = float(wc[0] if isinstance(wc, (list, tuple)) or hasattr(wc, "__len__") else wc)
                ww = float(wwd[0] if isinstance(wwd, (list, tuple)) or hasattr(wwd, "__len__") else wwd)
            except Exception:
                wl, ww = 40.0, 400.0
            if ww <= 0:
                wl, ww = 40.0, 400.0
            lo = wl - ww / 2.0
            v = np.clip((arr - lo) / ww, 0, 1)
            h, w = v.shape
            step = max(1, max(h, w) // maxdim)
            small = (v[::step, ::step] * 255 + 0.5).astype(np.uint8)
            np.save(cache, small)
            series["thumb"] = cache
            return small
        except Exception:
            return None

    def remove_study(self, study_uid):
        self.series = [s for s in self.series if s["study_uid"] != study_uid]
        self.save()
