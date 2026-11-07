# kindle-dash-server

Kindle Paperwhite 3（第7世代）の kindle-dash 用に、ダッシュボード画像を返すサーバーです。
HTMLテンプレートを Playwright（Chromium）で撮影し、8bitグレースケールPNGにして配信します。
既定は横置きで、1448×1072 の横長PNGを出力します。

表示するもの:
- 日付
- 天気（Open-Meteo、APIキー不要）
- Claude のプラン使用量（5時間枠・週間枠の残り。PCから送る方式）

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
> 最終的な見た目は Docker で確認してください。
> 再ビルドせずに試すには、`docker compose run --rm -p 8081:8080 -v ./src:/srv/src kindle-dash` を使います
> （プロジェクトは editable でインストールされているので、マウントした src がそのまま使われます）
> `sudo apt install fonts-noto-cjk` を入れれば、ローカルでも同じ見た目になります。

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
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | 画面の画素数（縦横どちらの順で書いてもよい） |
| `ORIENTATION` | `landscape`（横・既定）か `portrait`（縦） |
| `LANDSCAPE_ROTATE` | `0` なら横長PNGのまま出力。Kindle側で回転しないなら `90` か `270` にすると縦長PNGに回して出力（上下逆なら入れ替え） |
| `CACHE_SECONDS` | 同じ画像を使い回す秒数 |
| `SHOW_CLAUDE` | `0` で Claude 使用量パネルを隠す |
| `PUSH_TOKEN` | 設定すると使用量の送信にこのトークンが必要になる |
| `DATA_DIR` | 受け取った使用量の保存先（既定 `data/`、Docker では `./data` をマウント） |

## Claude の使用量を表示する

NAS には Claude のトークンを置きません。Claude Code を使う PC から、使用量を `POST /claude-usage` で送ります。
受け取った値は `data/claude_usage.json` に保存されます。
リセット時刻を過ぎた枠は「リセット済み」（残り100%）と表示されます。

### 方法A：statusline から自動で送る（おすすめ）

Claude Code は statusline のコマンドに、`rate_limits`（5時間・週間の使用率とリセット時刻）を含む JSON を渡します。
`~/.claude/settings.json` に次を追加すると、Claude Code を使うたびに送信されます。
内容が同じなら、送信は5分に1回までに抑えています。

```json
{
  "statusLine": {
    "type": "command",
    "command": "uv run --quiet --project /home/sumito/git/kindle-dash-server kindle-dash-push-claude --statusline --url http://<NASのIP>:8080"
  }
}
```

statusline には `5h 23% · 週 62%` と表示されます。
すでに別の statusline（例: `ccstatusline`）を使っている場合は `--passthrough ccstatusline` を付けます。
同じ JSON をそのコマンドに渡し、表示はそちらに任せます。
送信に失敗したときは60秒間送らないので、NAS が落ちていても statusline は遅くなりません。
`rate_limits` が入るのは Pro/Max のサブスクリプションでログインしているときだけです。

### 方法B：cron で定期的に送る

Claude Code のログイン情報（`~/.claude/.credentials.json`）のアクセストークンで使用量APIを叩き、その結果を送ります。
トークンの更新は Claude Code 本体に任せ、このスクリプトは読むだけです。

```bash
uv run --project /home/sumito/git/kindle-dash-server kindle-dash-push-claude --oauth --url http://<NASのIP>:8080
```

> 使用量API（`/api/oauth/usage`）は非公開のAPIなので、仕様が変わる可能性があります。
> `claude setup-token` で作る長期トークンには必要な権限（`user:profile`）が無いため、この方法では使えません。

### 動作確認

```bash
uv run kindle-dash-push-claude --file examples/claude_usage.json --url http://localhost:8080
curl http://localhost:8080/claude-usage
```

`PUSH_TOKEN` を設定した場合は、PC 側で `--token <値>` を付けるか、環境変数 `KINDLE_DASH_TOKEN` を設定します。

## Kindle側の設定

`/mnt/us/dashboard/local/fetch-dashboard.sh` の中にあるURL（GitHubのサンプル画像）を、
`http://<サーバーのIP>:8080/dash.png` に書き換えます。
KindleはHTTPSの証明書が古く失敗することがあるので、LAN内では `http://` を使います。

更新間隔は `/mnt/us/dashboard/local/env.sh` の cron 式で変えられます。

## 表示を増やすには

1. `src/kindle_dash_server/data.py` に取得処理を足して `collect()` の戻り値に入れる
2. `src/kindle_dash_server/templates/dashboard.html` に表示を足す
3. `uv run --env-file .env kindle-dash-snapshot` で確認 → `docker compose up -d --build`
