# Send to ICE Planner — Miele-LXIV Database プラグイン

Miele-LXIV の**患者リスト（データベース）で選択中のスタディ**を、スタンドアロン版
アプリ **「TIPS ICE Planner」** へワンアクションで受け渡すための小プラグイン。

## 使い方
1. Miele-LXIV の患者リストでスタディ（またはシリーズ）を **1つ選択**
2. メニュー **Plugins ▸ Database ▸ "Send to ICE Planner"**
3. TIPS ICE Planner が起動し、そのCTを自動で読み込む

> 右クリックメニューへの差し込みは次段階で追加予定（まず確実に動く「プラグインメニュー」版）。

## 仕組み（橋渡し）
`pluginType=Database` のプラグインは、選択時に `filterImage:` が呼ばれる
（実例: OsiriX 公式 `PDF to DICOM`）。本プラグインは：

1. `[[BrowserController currentBrowser] filesForDatabaseOutlineSelection:]` で
   選択スタディの DICOM ファイルパス一覧を取得
2. **サンドボックス内の一時フォルダ**（`NSTemporaryDirectory()` ＝Mieleコンテナの tmp）へコピー
   - Miele(MAS版)はサンドボックスのため、コンテナ外（例 `~/.tips_planner/`）へは書けない。
     コンテナ内に置けば、**非サンドボックスの TIPS ICE Planner 側が読み取れる**（権限の非対称性を利用）。
3. URLスキーム **`tipsiceplanner://open?dir=<コピー先>`** で
   `NSWorkspace openURL:` を使ってアプリを起動（LaunchServices 経由）

アプリ側（`app/main.py`）は `CFBundleURLTypes` でこのスキームを登録し、
`QFileOpenEvent` で受けて `dicom_io.load_series()` で読み込む。
アプリが既に起動中でも macOS が同じインスタンスへイベントを届けるため、二重起動しない。

## 前提
- TIPS ICE Planner（スタンドアロン版 **0.4.2 以降**）がインストール済で、
  **一度は起動済**であること（macOS にURLスキームを登録させるため）。
- Miele-LXIV（MAS版）に外部プラグインを読み込ませる設定（`disable-library-validation`、実証済）。

## ビルド・インストール
```bash
bash build.sh        # ad-hoc 署名でビルド→コンテナ Plugins へインストール（自分のMac検証用）
```
配布（後輩へ）する場合は Developer ID 署名＋公証が必要（`../plugin/release.sh` と同手順）。

## ファイル
- `Info.plist` … `pluginType=Database` / `MenuTitles=["Send to ICE Planner"]` / `NSPrincipalClass=TIPSSendFilter`
- `TIPSSendFilter.m` … 本体（選択取得→コピー→URL起動）。Miele API は非公開のため実機 `nm` と
  OsiriX/Horos の `browserController.h` から逆算宣言。
- `build.sh` … universal(arm64+x86_64) ビルド・署名・コンテナへインストール

## 注意（位置づけ）
研究・教育・自己研鑽用。医療機器ではなく、術中ナビでもない。最終判断は術者。
