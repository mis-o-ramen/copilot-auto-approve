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
- **Linux**: X11 環境が必要です (Wayland 非対応)。pyautogui の依存上 `sudo apt install python3-tk` も必要です。

## 使い方

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
