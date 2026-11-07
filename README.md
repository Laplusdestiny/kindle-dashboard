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
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` のトークン。設定するとコンテナ内で使用量を定期取得する |
| `CLAUDE_POLL_MINUTES` | 上の取得間隔（分、既定15）。`0` で取得しない |
| `DATA_DIR` | 受け取った使用量と、取得した時刻表の保存先（既定 `data/`、Docker では `./data` をマウント） |
| `SHOW_TRAIN` | `0` で電車の発車時刻パネルを隠す |
| `TRAIN_LABEL` | パネルの見出し（既定 `岡町 → 大阪梅田`） |
| `TRAIN_TIMETABLE_URL` | 阪急の駅時刻表ページのURL。`{day}` が `w`（平日）/ `h`（土曜・休日）に置き換わる（既定は岡町駅 宝塚線 大阪梅田方面） |
| `TRAIN_WINDOW_MINUTES` | 何分先までの発車を出すか（既定30） |

## Claude の使用量を表示する

使用量の取り方は次の3通りです。どれで取った値も `data/claude_usage.json` に保存され、新しい値が表示に使われます。
リセット時刻を過ぎた枠は「リセット済み」（残り100%）と表示されます。

### 方法A：コンテナ内の Claude Code で取得する（NAS だけで完結）

イメージには Claude Code（ネイティブ版、バージョン固定）が入っています。
`CLAUDE_CODE_OAUTH_TOKEN` を設定すると、サーバーが起動直後と `CLAUDE_POLL_MINUTES`（既定15分）ごとに次のことをします。

1. haiku に「OK」とだけ返させる最小のリクエストを送る（`claude -p`、ツール・設定・MCP はすべて無効）。
2. そのとき Claude Code が出す `rate_limit_event` から、5時間・週間の使用率を読む。

1回の取得は約900トークンなので、プランの枠はほとんど減りません。

1. PC で長期トークン（1年有効）を作ります。

   ```bash
   claude setup-token
   ```

2. NAS の `docker-compose.yml` と同じ場所に `.env` を作り、トークンを書きます。

   ```bash
   CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
   ```

3. `docker compose up -d --build` で起動し、`docker compose logs` に `Claude 使用量を更新` と出れば成功です。

> トークンが漏れると、あなたのプランで推論ができてしまいます。`.env` は git に入れないでください（`.gitignore` 済み）。
> このトークンでは使用量API（`/api/oauth/usage`）は呼べません（権限 `user:profile` が無いため）。そのため Claude Code の出力から読む方式にしています。

### 方法B：PC の statusline から自動で送る

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

### 方法C：PC の cron で定期的に送る

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

## 電車の発車時刻

阪急公式サイトの駅時刻表ページ（平日・土曜休日の2枚）を取得して、今から30分以内の発車を表示する。

- 時刻表は1日1回だけ取り直して `data/train/` に保存する。取得に失敗しても前回の分で表示を続ける。
- 平日か土休日かは、内閣府の祝日CSV（月1回取得）で判定する。取れないときは土日だけで判定する。
- 3時より前は前日のダイヤとして扱う。終電後は「次は 4:40」のように始発を出す。
- 年末年始などの臨時ダイヤには対応していない。
- 別の阪急の駅・方面にするには、駅ページ（例 `https://www.hankyu.co.jp/station/okamachi.html`）の時刻表リンク
  `/station/html/HK-45_ta_1_w.html` の `_w` を `_{day}` にしたURLを `TRAIN_TIMETABLE_URL` に入れる。

## Kindle側の設定

`/mnt/us/dashboard/local/fetch-dashboard.sh` の中にあるURL（GitHubのサンプル画像）を、
`http://<サーバーのIP>:8080/dash.png` に書き換えます。
KindleはHTTPSの証明書が古く失敗することがあるので、LAN内では `http://` を使います。

更新間隔は `/mnt/us/dashboard/local/env.sh` の cron 式で変えられます。

## 表示を増やすには

1. `src/kindle_dash_server/data.py` に取得処理を足して `collect()` の戻り値に入れる
2. `src/kindle_dash_server/templates/dashboard.html` に表示を足す
3. `uv run --env-file .env kindle-dash-snapshot` で確認 → `docker compose up -d --build`
