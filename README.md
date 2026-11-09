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
cp .env.example .env    # 初回だけ。設定を変えるならこの .env を編集する
docker compose up -d --build
```

`docker-compose.yml` は `.env` をそのままコンテナに渡すだけで、設定値は持っていません。
`.env` が無いと起動できないので、空でもよいので作っておいてください。

- `http://<サーバーのIP>:8080/dash.png` … Kindleが取りに来る画像
- `http://<サーバーのIP>:8080/preview` … PCのブラウザでレイアウト確認用
- `http://<サーバーのIP>:8080/health` … 動作確認

イメージ内でも `uv sync --locked` で `uv.lock` どおりの版を入れるので、ローカルとNASで依存がずれません。

## 設定（.env）

設定はすべて `.env` に書きます。ローカル（`uv run --env-file .env`）と Docker（`docker compose` の `env_file`）が同じファイルを読みます。
`.env` に書いていない項目や、値が空の項目はコードの既定値になります。既定値は本番（Kindle で横置き）向けです。

| 変数 | 内容 |
|---|---|
| `TZ` | タイムゾーン（既定 `Asia/Tokyo`） |
| `LATITUDE` / `LONGITUDE` / `PLACE_NAME` | 天気を取る地点と表示名 |
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | 画面の画素数（縦横どちらの順で書いてもよい） |
| `ORIENTATION` | `landscape`（横・既定）か `portrait`（縦） |
| `LANDSCAPE_ROTATE` | 横長のデザインを回して、縦長PNG（1072×1448）で出力する（既定 `90`）。`90` は Kindle の下端（電源ボタン・USB）が左に来る向き。上下が逆なら `270`。`0` だと横長PNGのままになり、Kindle では右側が切れる |
| `CACHE_SECONDS` | 同じ画像を使い回す秒数（既定60。ローカルで編集しながら見るなら `0`） |
| `SHOW_CLAUDE` | `0` で Claude の行を隠す |
| `SHOW_CODEX` | `0` で Codex の行を隠す |
| `SHOW_AGY` | `0` で Antigravity の行を隠す |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` のトークン。設定するとコンテナ内で使用量を定期取得する |
| `CLAUDE_POLL_MINUTES` | 上の取得間隔（分、既定15）。`0` で取得しない |
| `CODEX_ACCESS_TOKEN` | Codex のアクセストークン（Business / Enterprise のみ）。個人プランは `codex login --device-auth` でログインする |
| `CODEX_POLL_MINUTES` | コンテナ内で Codex 使用量を取得する間隔（分、既定15）。`0` で取得しない |
| `AGY_POLL_MINUTES` | コンテナ内で Antigravity 使用量を取得する間隔（分、既定15）。`0` で取得しない |
| `DATA_DIR` | 取得した使用量・ログイン情報・時刻表の保存先（既定 `data/`、Docker では `./data` をマウント） |
| `SHOW_TRAIN` | `0` で電車の発車時刻パネルを隠す |
| `TRAIN_LABEL` | パネルの見出し（既定 `岡町 → 大阪梅田`） |
| `TRAIN_TIMETABLE_URL` | 阪急の駅時刻表ページのURL。`{day}` が `w`（平日）/ `h`（土曜・休日）に置き換わる（既定は岡町駅 宝塚線 大阪梅田方面） |
| `TRAIN_WINDOW_MINUTES` | 何分先までの発車を出すか（既定30） |

## AI の使用量を表示する（Claude・Codex・Antigravity）

左下の表に、各サービスの残り（5時間枠・週間枠）を出します。
- 取得はすべてコンテナ内で行います。PC から送る必要はありません。
- 取得した値は `data/claude_usage.json` / `data/codex_usage.json` / `data/agy_usage.json` に保存されます。
- リセット時刻を過ぎた枠は「リセット済」（残り100%）と表示します。
- ログインしていないサービスは、`docker compose logs` に案内が1回出ます。

| サービス | 取り方 | ログイン | 取得間隔 |
|---|---|---|---|
| Claude | `claude -p` を最小構成で1回動かし、出力の `rate_limit_event` を読む（約900トークン） | `.env` の `CLAUDE_CODE_OAUTH_TOKEN` | `CLAUDE_POLL_MINUTES` |
| Codex | `codex app-server` の `account/rateLimits/read`（トークン消費なし） | コンテナ内で `codex login --device-auth`（個人プラン）か `.env` の `CODEX_ACCESS_TOKEN` | `CODEX_POLL_MINUTES` |
| Antigravity | `agy -p /usage --output-format json`（トークン消費なし） | コンテナ内で一度ログイン | `AGY_POLL_MINUTES` |

### Claude

イメージには Claude Code（ネイティブ版、バージョン固定）が入っています。
`CLAUDE_CODE_OAUTH_TOKEN` を設定すると、サーバーが起動直後と `CLAUDE_POLL_MINUTES`（既定15分）ごとに次のことをします。

1. haiku に「OK」とだけ返させる最小のリクエストを送る（`claude -p`、ツール・設定・MCP はすべて無効）。
2. そのとき Claude Code が出す `rate_limit_event` から、5時間・週間の使用率を読む。

1回の取得は約900トークンなので、プランの枠はほとんど減りません。

1. PC で長期トークン（1年有効）を作ります。

   ```bash
   claude setup-token
   ```

2. NAS の `docker-compose.yml` と同じ場所にある `.env` に、トークンを書きます。

   ```bash
   CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
   ```

3. `docker compose up -d --build` で起動し、`docker compose logs` に `Claude 使用量を更新` と出れば成功です。

> トークンが漏れると、あなたのプランで推論ができてしまいます。`.env` は git に入れないでください（`.gitignore` 済み）。
> このトークンでは使用量API（`/api/oauth/usage`）は呼べません（権限 `user:profile` が無いため）。そのため Claude Code の出力から読む方式にしています。

### Codex

イメージには Codex CLI が入っています（Dockerfile の `CODEX_VERSION` で固定）。
使用量は `codex app-server` の `account/rateLimits/read`（Codex 公式のプロトコル）で読みます。トークンは消費せず、アカウントIDなどは保存しません。
ログイン情報は `CODEX_HOME`（`/srv/data/codex`、つまり NAS の `./data/codex`）に保存されるので、再起動しても残ります。

ログインのしかたは、プランによって次のどちらかです。

- **個人プラン（Free / Plus / Pro など）**：コンテナ内で一度だけデバイスコードでログインします。

  ```bash
  docker compose exec kindle-dash codex login --device-auth
  ```

  表示されたURLをPCのブラウザで開き、コードを入力します。
  これは NAS 専用の別のログインなので、PC の Codex のログインには影響しません。
  - ログアウトしたいときは、`docker compose exec kindle-dash codex logout` を実行します。
  - PC の `~/.codex/auth.json` を NAS にコピーするのはやめてください。更新用のトークンを共有することになり、どちらかが更新すると、もう一方のログインが切れるおそれがあります。

- **Business / Enterprise ワークスペース**：管理画面で発行したアクセストークンを `.env` に書きます（個人プランでは発行できません）。

  ```bash
  CODEX_ACCESS_TOKEN=...
  ```

  サーバーは起動時に `codex login --with-access-token` でログインしてから取得します（app-server はこの環境変数を直接は読まないため）。

枠の名前は `windowDurationMins` から作ります。プランによっては枠が1つだけのこともあります（Free プランは30日枠のみで、「週間」の列に「30日枠」と添えて出します）。

### Antigravity CLI（agy）

イメージには agy が入っています。公式インストーラと同じ配布元から、Dockerfile の `AGY_BUILD` でビルド番号まで固定しています。
`agy -p /usage --output-format json` を実行すると、モデルを呼ばずに（トークン 0 で）クォータが JSON で返ります。それを起動直後と `AGY_POLL_MINUTES`（既定15分）ごとに読みます。
表には系統ごと（Gemini・Claude/GPT・Argon）に、5時間枠と週間枠を出します。

ログインはコンテナ内で一度だけ行います。`~/.gemini` を `./data/agy` に向けているので、再起動しても残ります。

```bash
docker compose exec -it kindle-dash agy -p /usage
```

1. 表示されたURLをPCのブラウザで開き、Google アカウントでログインします。
2. 表示されたコードをターミナルに貼ります。
3. 使用量が表示されれば成功です。

これも NAS 専用の別のログインです。

- 未ログインのまま `agy -p` を実行すると、ログインを60秒待ってから失敗します。そのため、ログインするまでは取得のたびに60秒かかりますが、画像の描画は止まりません。
- agy は通常の実行中に自分で更新する仕組みを持っています（止める設定は公開されていません）。イメージを作り直すと、`AGY_BUILD` の版に戻ります。

### 表示の確認（ローカル）

データがある状態の見た目は、サンプルを `data/` に保存してから描画すると確認できます。

```bash
uv run python -c "
import json
from kindle_dash_server import agy, claude, codex
claude.save(json.load(open('examples/claude_usage.json')))
codex.save(json.load(open('examples/codex_usage.json')))
agy.save(json.load(open('examples/agy_usage.json')))
"
LANDSCAPE_ROTATE=0 uv run --env-file .env kindle-dash-snapshot -o out/dash.png
```

保存されている内容は `GET /claude-usage`・`/codex-usage`・`/agy-usage` でも見られます。

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

画面の右下に電池残量を出すには、残量を `battery` パラメータで付けて取りに来るようにします。
`gasgauge-info -c` は `82%` のように返します。サーバー側で数字だけを読むので、`%` が付いたままでも構いません。

```sh
#!/usr/bin/env sh
# kindle-dash 付属の xh を使う場合（param==value でクエリを付ける）
"$(dirname "$0")/../xh" -d -q -o "$1" get "http://<サーバーのIP>:8080/dash.png" battery=="$(gasgauge-info -c)"
```

```sh
#!/usr/bin/env sh
# curl を使う場合
curl -fsS -o "$1" "http://<サーバーのIP>:8080/dash.png?battery=$(gasgauge-info -c | tr -d '%')"
```

- 残量が前回から変わると、キャッシュがあっても描き直します。
- 20%以下は太字の墨色で目立たせます。
- 6時間以上届いていなければ、電池の表示を消します。Kindle が止まっているかもしれないためです。

更新間隔は `/mnt/us/dashboard/local/env.sh` の cron 式で変えられます。

## 表示を増やすには

1. `src/kindle_dash_server/data.py` に取得処理を足して `collect()` の戻り値に入れる
2. `src/kindle_dash_server/templates/dashboard.html` に表示を足す
3. `uv run --env-file .env kindle-dash-snapshot` で確認 → `docker compose up -d --build`
