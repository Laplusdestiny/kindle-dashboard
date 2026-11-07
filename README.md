# kindle-dash-server

Kindle Paperwhite 3（第7世代）の kindle-dash 用に、ダッシュボード画像を返すサーバーです。
HTMLテンプレートを Playwright（Chromium）で撮影し、1072×1448 の8bitグレースケールPNGにして配信します。
天気は Open-Meteo（APIキー不要）から取得します。

## ローカルで確認・修正（uv）

```bash
uv sync                                  # 依存を .venv に入れる
uv run playwright install chromium       # 初回だけ。撮影用のChromium
cp .env.example .env                     # 設定（地点・向きなど）
```

**画像を1枚書き出して見る**（サーバー不要・いちばん手軽）

```bash
uv run --env-file .env kindle-dash-snapshot -o out/dash.png
```

**サーバーを立てて見る**（`--reload` でコード・テンプレートの変更を自動反映）

```bash
uv run --env-file .env kindle-dash-server --reload
```

- `http://localhost:8080/dash.png` … Kindleが取りに来る画像（実際の見た目）
- `http://localhost:8080/preview` … 撮影前のHTML（ブラウザの開発者ツールでCSS調整用）

> ローカルに Noto CJK フォントが無いと別のフォントで描画されます。
> 最終的な見た目は下の Docker で確認してください
> （`sudo apt install fonts-noto-cjk` を入れればローカルでも同じになります）。

依存を追加するときは `uv add <パッケージ>`（`pyproject.toml` と `uv.lock` が更新されます）。

## NASで起動（Docker）

```bash
docker compose up -d --build
```

- `http://<サーバーのIP>:8080/dash.png` … Kindleが取りに来る画像
- `http://<サーバーのIP>:8080/preview` … PCのブラウザでレイアウト確認用
- `http://<サーバーのIP>:8080/health` … 動作確認

イメージ内でも `uv sync --locked` で `uv.lock` どおりの版を入れるので、ローカルとNASで依存がずれません。

## 設定（docker-compose.yml の environment / ローカルは .env）

| 変数 | 内容 |
|---|---|
| `LATITUDE` / `LONGITUDE` / `PLACE_NAME` | 天気を取る地点と表示名 |
| `ORIENTATION` | `portrait`（縦）か `landscape`（横） |
| `LANDSCAPE_ROTATE` | 横置きで上下が逆なら `90` と `270` を入れ替え |
| `CACHE_SECONDS` | 同じ画像を使い回す秒数 |

## Kindle側の設定

`/mnt/us/dashboard/local/fetch-dashboard.sh` の中にあるURL（GitHubのサンプル画像）を、
`http://<サーバーのIP>:8080/dash.png` に書き換えます。
KindleはHTTPSの証明書が古く失敗することがあるので、LAN内では `http://` を使います。

更新間隔は `/mnt/us/dashboard/local/env.sh` の cron 式で変えられます。

## 表示を増やすには

1. `src/kindle_dash_server/data.py` に取得処理を足して `collect()` の戻り値に入れる
2. `src/kindle_dash_server/templates/dashboard.html` に表示を足す
3. `uv run --env-file .env kindle-dash-snapshot` で確認 → `docker compose up -d --build`
