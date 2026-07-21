"""術式プリセット（このアプリ固有の定数だけを置く）。

共有インフラ（catalog / updater / ts_seg 経由の ts_helper）はここを読む。
engine（共通部品正本）の配布対象では**ない**＝アプリごとに中身が違ってよい唯一の場所。
Phase 2 で engine/procedures/ の術式プラグインに発展させる予定（設計図 §3）。
"""

# ── app_boot: アプリ名と URL スキーム ─────────────────────────
APP_DISPLAY_NAME = "TIPS ICE Planner"        # QApplication.applicationName / macメニューバー表示
URL_SCHEME = "tipsiceplanner"                # Mieleプラグイン→本アプリの橋渡し用URLスキーム

# ── catalog: アプリデータの保存先 ─────────────────────────────
DATA_SUBDIR = "TIPSPlanner"           # QStandardPaths AppDataLocation 配下のフォルダ名
LEGACY_DATA_DIR = "~/.tips_planner"   # PySide6 が無い環境のフォールバック

# ── updater: 配布物の名前 ─────────────────────────────────────
DIST_LOCAL_DIRS = ["~/Desktop/TIPS ICE Planner 配布"]
DIST_APP_BASENAME = "TIPS ICE Planner"             # + ".app" / ".exe"
DIST_ZIP_MAC = "TIPS-ICE-Planner-Mac.zip"
DIST_ZIP_WIN = "TIPS-ICE-Planner-Windows.zip"

# ── TotalSegmentator: total タスクで取る構造 ──────────────────
# ts_seg が ts_helper へ --total-rois として渡す（門脈本幹＋脾静脈は1ラベル）
# 抽出は広く・表示は絞る方針（2026-07-18）。total タスクは1回の推論で117構造を出すので、
# roi_subset を広げても **推論時間はほぼ増えない**（同じ結果から切り出すだけ）。先に取っておけば、
# 後で表示を増やすとき AI の再解析が要らない。実際に3Dへ出すのは TS_EXTRA_ORGANS で選ぶ。
TOTAL_ROIS = ["liver", "inferior_vena_cava", "portal_vein_and_splenic_vein",
              # ↓ TIPS 穿刺の回避目標・方位ランドマーク（抽出のみ。表示は TS_EXTRA_ORGANS）
              "gallbladder", "colon", "kidney_right", "aorta",
              "stomach", "duodenum", "spleen"]

# ── Pane3D（3D linkage）の術式表示 ────────────────────────────
PANE3D_AI_BTN = "Structure AI"                  # 左上AIボタンのラベル
PANE3D_SCOPE_STYLE = "ice"                   # 明灰シャフト＋偏向先端＋アレイ（AcuNav ICE）
PANE3D_GHOST_COLOR = (214, 150, 120)         # AIゴースト＝肝臓（陰影付き面）
PANE3D_GHOST_OPACITY = 0.5
PANE3D_TARGET_COLOR = (255, 90, 80)          # Target/apex マーカー＝REDC
PANE3D_EMPTY_HINT = ("Draw the ICE path (or press Structure AI) to show 3D",
                     "IVCパスを描く（または構造AIを押す）と3Dが表示されます")
PANE3D_LEGEND = ("metal=cannula & needle  white dashes=advance dir  cyan=ICE sector",
                 "金属=外筒・針  白破線=進行方向  水色=ICE扇")

# ── 構造認識AI（ts_seg.py が読む・Phase 3 統一）───────────────
TS_SCENE_MODE = "fixed3"      # 3Dシーンの流儀: 肝ゴースト + IVC/門脈/肝血管ツリー
TS_ROIS_VERSION = "v2-tips10"  # 抽出セットのバージョン（キャッシュキーに混入＝変えると自動で作り直す）
TS_DEFAULT_SHOW = ()          # organs モード用の既定表示（fixed3 では未使用）
# 主シーンに *加えて* 3D に色分け表示する構造（色は ts_seg.ORGAN_COLORS）。
# 穿刺経路が通ってはいけないもの＝胆嚢・結腸、および肝腫瘍（HCC 合併例で経路の可否判断に効く）。
# 増減は 🟥 医学的判断（山本先生）。ここを書き換えるだけで反映され、AI の再解析は不要。
TS_EXTRA_ORGANS = ("gallbladder", "colon", "liver_tumor")

# ── ビーム幾何（tips_core/geometry.py が読む）─────────────────
FAN_HALF_DEG = 45.0    # 扇の半角＝90°セクター（AcuNav ICE）
R_DEPTH_MM = 85.0      # 描出深達 mm
PATH_PARAM = "z"       # プローブ経路の座標: IVC 芯線は体軸単調なので Zスライス値

# ── アプリ表記・リンク・起動時ヒント（main.py から移動、Phase 3c-2）──
GITHUB_REPO = "https://github.com/breguetjp-gif/tips-ice-planner"
# 開発支援の窓口（先生指示 2026-07-21）：英語＝GitHub Sponsors／日本語＝note（先生の HP）。
SPONSORS_URL = "https://github.com/sponsors/breguetjp-gif"   # 英語(既定)＝GitHub Sponsors
SPONSORS_URL_JA = "https://note.com/medical_app"            # 日本語＝note（先生の募金ページ）
AUTHOR_LINE = "Masayoshi Yamamoto — Department of Radiology, Teikyo University School of Medicine, Tokyo, Japan"
FEEDBACK_FORM_JA = "https://forms.gle/nwupwhXBxhRo7vTq7"     # フィードバック用Googleフォーム（日本語版）
FEEDBACK_FORM_EN = "https://forms.gle/3A5ncd6f7xw6rjUC6"     # フィードバック用Googleフォーム（英語版）

# 起動時の「今日のヒント」（VS Code風）。(en, ja) のタプル。0番目はWelcome的な内容にしている。
TIPS_EN_JA = [
    ("Welcome to TIPS ICE Planner! Start with Step 1 (ICE setup): click along the IVC on the "
     "Axial pane to set the probe's path. See Help → User manual for the full walkthrough.",
     "TIPS ICE Plannerへようこそ！まずはStep 1（ICEセットアップ）"
     "から：Axial画面でIVCに沿ってクリックし、"
     "プローブの通り道を設定します。"
     "詳しい手順はヘルプ→使い方説明書をご覧ください。"),
    ("Pinch or ⌘+scroll to zoom any image pane — it stays centered on your cursor.",
     "ピンチまたは⌘+スクロールで拡大縮小できます。"
     "カーソル位置が中心になります。"),
    ("Right-click any pane (including the 3D linkage panel) to reset its zoom and pan.",
     "各画面（3D連動パネルも含む）を右クリックすると、"
     "拡大・位置をリセットできます。"),
    ("Save (1/2/3) keeps up to three working states per patient — Restore them anytime from the patient list.",
     "保存（1・2・3）で患者ごとに最大3つの作業状態を残せます。"
     "呼び戻しは患者リスト画面からいつでもできます。"),
    ("The actual-needle shape is only drawn bold with a slice/plane label when it truly lies in "
     "the cross-section you're viewing right now.",
     "実際の針の形は、今見ている断面に本当に乗っているときだけ、"
     "太く強調表示されラベルが付きます。"),
    ("Switch the whole interface between English and Japanese anytime with the button, top-right.",
     "右上のボタンでいつでも英語⇄日本語を切り替えられます。"),
    ("The Liver ghost overlays a translucent liver outline in 3D to help judge the fan's position.",
     "肝臓ゴーストは、扇の位置関係を把握するための"
     "半透明な肝臓輪郭です。"),
    ("Roll freely rotates the ICE image for a clearer view without changing the underlying geometry.",
     "ロールはICE画像を任意角度で回して見やすくします"
     "（幾何学的な位置関係は変わりません）。"),
    ("You can drag an IVC-path point, or Entry/Target, after placing it to fine-tune its position.",
     "IVCパスの点やEntry/Targetは、置いた後もドラッグで"
     "微調整できます。"),
    ("Clear lets you remove just the IVC path, the needle, or everything — one patient at a time.",
     "クリアでは、IVCパスだけ・針だけ・全部、"
     "と個別に消去できます。"),
    ("Transabdominal mode simulates a surface probe — click the skin on any CT pane to place it.",
     "経腹モードは体表プローブを模擬します。"
     "CT断面の皮膚をクリックして設置します。"),
]
