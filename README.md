# copilot-auto-approve

画面上に **Allow ボタン**が表示されたことを検知し、自動でクリックする Python スクリプトです。
`images/` に置いたボタン画像を OpenCV のテンプレートマッチングで画面から探し、見つかったらその中心をクリックします。

## セットアップ

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate / macOS・Linux: source .venv/bin/activate
pip install -r requirements.txt
```

- **macOS**: 「システム設定 → プライバシーとセキュリティ」で、ターミナル (または使う IDE) に
  **画面収録** と **アクセシビリティ** の権限を与えてください。
- **Linux**: X11 環境が必要です (Wayland 非対応)。`sudo apt install python3-tk` も必要です。
- GUI は Python 標準の tkinter を使います。macOS の Homebrew 版 Python では `brew install python-tk` が必要な場合があります。

## GUI アプリ

```bash
python auto_approve_gui.py
```

ターミナルを使わずに起動したい場合は、ランチャーをダブルクリックしてください
(`.venv` があれば自動的にそちらの Python を使います)。

- **Windows**: `AutoApprove.pyw`
- **macOS**: `AutoApprove.command` (初回は右クリック →「開く」)

| 機能 | 説明 |
| --- | --- |
| ON / OFF | 右上のスイッチ、ウィンドウ上でスペースキー、またはグローバルホットキー (既定 `Ctrl+Alt+A`) |
| ステータス | ● 緑=監視中 / 青=検知のみ / 黄=操作待ち / 灰=停止中 / 赤=エラー。クリック数・最終クリック時刻・稼働時間をタイル表示 |
| 最前面 | チェックで常に最前面に表示 |
| 検知のみ | クリックせずに検知だけ行う (動作確認用) |
| 自動OFF | 指定時間 (15分〜2時間) 経過で自動停止。付けっぱなし防止 |
| 操作中はクリックしない | キーボード/マウス操作から指定秒数 (既定 1.5 秒) 経つまでクリックを保留。作業中にマウスを奪われない |
| トレイ常駐 | タスクトレイのアイコン色で状態を表示。クリックで ON/OFF、メニューから表示/終了。「× でトレイに格納」でウィンドウを隠して常駐 |
| 設定とログ ▾ | しきい値・チェック間隔・操作中の保留・ホットキー・クリック音・起動時自動ON・テーマ・トレイ の設定 (設定タブ) とログ (ログタブ) |
| テーマ | Windows 11 風の [Sun Valley](https://github.com/rdbende/Sun-Valley-ttk-theme) テーマ。ライト / ダーク / システムに合わせる から選択 |

- 設定は `settings.json` に保存され、次回起動時に復元されます
- ボタン画像は ON にするたびに `images/` から読み直すので、画像を追加しても再起動は不要です
- ホットキーは [pynput](https://pynput.readthedocs.io/) の書式 (`<ctrl>+<shift>+x` など) で指定します。
  macOS では tkinter との併用で不安定なことがあるため既定で無効です
  (有効にする場合はアクセシビリティ/入力監視の権限が必要)
- トレイ常駐は Windows で既定 ON です。macOS では tkinter と両立しないため使えず、Linux はデスクトップ環境次第です

## 実行ファイルのビルド (Python 不要で配布)

```bash
pip install -r requirements.txt pyinstaller
python build.py
```

- **Windows**: `dist/AutoApprove/AutoApprove.exe` (フォルダごと配布)
- **macOS**: `dist/AutoApprove.app` (画像は `dist/images/`)

ボタン画像と `settings.json` は実行ファイル (macOS は `.app`) と同じ場所の `images/` / `settings.json` を使います。
GitHub Actions の **Build app** ワークフロー (手動実行 / `v*` タグ) でも Windows・macOS 版を作成でき、成果物は Artifacts からダウンロードできます。
macOS でビルドしたアプリを使う場合は、アプリ自体に画面収録・アクセシビリティの権限を与えてください。

## CLI の使い方

1. Allow ボタンをスクリーンショットで切り抜き、`images/allow.png` として保存
2. まず検知だけ試す:

   ```bash
   python auto_approve.py --dry-run
   ```

   `検知: allow.png score=0.98 -> (x, y)` のように出れば OK
3. 本番実行:

   ```bash
   python auto_approve.py
   ```

**停止方法**: `Ctrl+C`、またはマウスを画面の**左上隅**へ移動 (pyautogui の FAILSAFE)。

### 主なオプション

| オプション | 既定値 | 説明 |
| --- | --- | --- |
| `templates...` | `images/` | ボタン画像ファイル/ディレクトリ (複数可) |
| `-t, --threshold` | `0.85` | 一致とみなすスコア (0〜1)。誤検知するなら上げ、検知しないなら下げる |
| `-i, --interval` | `1.0` | 画面チェック間隔 (秒) |
| `--cooldown` | `1.5` | クリック後の待機秒数 (連打防止) |
| `--scales` | `1.0` | テンプレートの拡大率。表示倍率が違う画面用に `0.8,1.0,1.25` など |
| `--monitor` | `0` | 0=全モニター, 1=メイン, 2=... |
| `--region` | なし | 検索範囲 `left,top,width,height` (高速化・誤検知防止) |
| `--dry-run` | | 検知のみでクリックしない |
| `--once` | | 1 回クリック (dry-run では検知) したら終了 |
| `--no-restore-mouse` | | クリック後マウスを元の位置に戻さない |
| `--pause-when-active SEC` | `0` | キーボード/マウス操作から SEC 秒経つまでクリックしない |
| `--test-image PATH` | | 画面の代わりに画像ファイルで検知テスト |

### うまく検知できないとき

- `--dry-run -t 0.6` で実行し、表示される score を確認 → 適切な閾値を決める
- 画面の表示倍率 (Windows の 125% など) を変えた場合は、画像を撮り直すか `--scales` を指定
- 似たボタンを誤検知する場合は、閾値を上げるか `--region` で範囲を絞る

## テスト

```bash
pip install pytest
python -m pytest
```

## 注意

画面上のボタンを無条件に承認するツールです。意図しない操作まで承認してしまう可能性があるため、
信頼できる作業中のみ使用してください。
