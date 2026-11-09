# kindle-dash-server

脱獄した Kindle Paperwhite 3（第7世代、1072×1448）の kindle-dash 向けに、ダッシュボード画像（8bitグレースケールPNG）を返すサーバー。
NAS 上の Docker で動かす。開発と確認はこの PC で uv を使って行う。

## コマンド

```bash
uv sync                                                      # 依存を入れる
uv run playwright install chromium                           # 初回だけ
uv run --env-file .env kindle-dash-snapshot -o out/dash.png  # PNGを1枚書き出す（サーバー不要）
uv run --env-file .env kindle-dash-server --reload           # サーバー起動（.py/.html の変更で再起動）
docker compose build                                         # 本番と同じイメージ
```

- 設定は `.env` に集約している（gitignore 済み、雛形は `.env.example`）。ローカルは `uv run --env-file .env`、Docker は compose の `env_file: .env` で同じファイルを読む。
  - `docker-compose.yml` には設定値を書かない。既定値はコードの `os.getenv("X") or "既定"` にだけ置く（空の値も既定扱いにするため `os.getenv("X", "既定")` は使わない）。`TZ` だけは Dockerfile の `ENV` で既定を入れている。
  - 設定を増やしたら、コードの既定値・`.env.example`（コメントで既定値を示す）・README の表の3か所をそろえる。
- `uv run --env-file` は、すでにシェルにある環境変数を上書きしない。そのため一時的な切り替えは `ORIENTATION=portrait uv run --env-file .env ...` のように書ける。
- 確認用の出力は `out/` に、受け取った Claude 使用量は `data/` に置く（どちらも gitignore）。

## 構成と流れ

`src/kindle_dash_server/`（uv_build の src レイアウト。editable でインストールされる）

- `data.py` … `collect()` がテンプレートに渡す値を集める（日付、Open-Meteo の天気、`claude.view()`、`train.view()`）。
- `templates/dashboard.html` … Jinja2 テンプレート。`body.landscape` / `body.portrait` でレイアウトを切り替える。
- `main.py` … FastAPI のアプリ本体。
  - 描画の流れ：HTML を Playwright の Chromium で撮影 → `to_kindle_png()` でグレースケール化 → 16階調に丸める →（必要なら回転）→ PNG。
  - エンドポイント：`/dash.png`（`CACHE_SECONDS` の間キャッシュ。`?battery=82` で Kindle の電池残量も受け取る）、`/preview`（撮影前のHTML）、`POST` / `GET /claude-usage`、`POST` / `GET /codex-usage`、`POST` / `GET /agy-usage`、`/health`。
- `claude.py` … Claude 使用量を受け取り（`normalize`）、`data/claude_usage.json` へ保存し、表示用に整形する（`view`）。
- `train.py` … 阪急の駅時刻表ページを1日1回取得して解析し（`parse_timetable`）、`data/train/` に保存する。平日・土休日は内閣府の祝日CSVで判定し、`TRAIN_WINDOW_MINUTES` 分以内の発車を返す（`view`）。
  - 電車パネルを出すと `body.with-train` が付き、天気などの余白を詰めて1画面に収める。縦置きでは、3時間ごとの予報と3日分の予報を隠している。
  - AI の使用量は、1行1サービスの表（`.usage`）にしている。列は「5時間」「週間」。Antigravity は系統（Gemini・Claude/GPT・Argon）ごとに行を分け、見出し行を1つ置く。サービスや枠が増えると行が増えるので、追加したら横置き・縦置きの両方で収まるかを見る。
- `codex.py` … Codex 使用量を `codex app-server` で読み（`fetch_rate_limits`。コンテナ内の定期取得 `poll_loop` と PC 側の送信コマンドで共用）、受け取った値（`normalize`）を `data/codex_usage.json` へ保存する。
- `agy.py` … Antigravity CLI の使用量（statusline JSON の `quota`）を受け取り、`data/agy_usage.json` へ保存する。
- `usage.py` … Claude・Codex・Antigravity の保存データから、使用量パネルの表（`rows`）を作る。どの枠を「5時間」「週間」のどちらの列に入れるかは、枠の長さ（分）で決める。
  - 保存・読み込みは `claude.py` の `write_json` / `read_json` を共用している。
- `kindle.py` … `/dash.png?battery=` で届いた Kindle の電池残量を `data/kindle.json` に保存し、フッター右に出す（6時間より古ければ出さない）。
- `push.py` … PC 側で動かす送信コマンド `kindle-dash-push-claude`。`--statusline`、`--oauth`、`--file` の3方式。
- `push_codex.py` … PC 側で動かす送信コマンド `kindle-dash-push-codex`。`codex app-server` と JSON-RPC で話して `account/rateLimits/read` を呼ぶ。`--hook` は Codex の `Stop` フック用。
- `push_agy.py` … PC 側で動かす送信コマンド `kindle-dash-push-agy`。agy の statusline から呼ばれ、`quota` と `plan_tier` だけを `POST /agy-usage` に送る。
- `__init__.py` … `kindle-dash-server` / `kindle-dash-snapshot` の入口。

向きは `ORIENTATION` だけで決まる。`SCREEN_WIDTH` / `SCREEN_HEIGHT` はどちらの順で書いても同じ結果になる。
`LANDSCAPE_ROTATE=0` で横長PNG（1448×1072）、`90` / `270` で縦長PNGに回して出力する（Pillow の `rotate` なので反時計回り）。本番は `90`。

## 変更後の確認（必須）

テンプレート・CSS・表示データを変えたら、次の両方で PNG を書き出して目で見る。

1. **ローカルで横置き**（本番の設定）：`uv run --env-file .env kindle-dash-snapshot -o out/dash.png` を実行し、その PNG を読んで確認する。
   - 本番は90°回した縦長PNGになる。レイアウトを見るときは `LANDSCAPE_ROTATE=0 uv run --env-file .env ...` で回さずに書き出すと見やすい。
2. **Docker でも1回**：ローカルには Noto CJK フォントが無く、文字の幅や高さが違う。そのため、はみ出しは Docker 上でしか分からないことがある。
   - 再ビルドは不要。`-v $PWD/src:/srv/src:ro` で src をマウントすれば、そのまま反映される。
   - ポート 8080 はユーザーが自分でサーバーを起動して使っていることが多いので、確認用には別のポート（例 18081）を使う。

確認するのは、文字がはみ出していないか・フッターが切れていないか・折り返しで崩れていないか。
Claude パネルは、データがある状態で見る：`uv run kindle-dash-push-claude --file examples/claude_usage.json --url ...`。

確認用に起動したサーバーは PID を指定して止める（`ss -ltnp` で PID を調べる）。`pkill -f` は、パターンが自分のシェルのコマンドラインにも一致して、シェルごと落ちる。

## E-Ink のデザインルール

- 白地・墨（#000）・灰2段階（`--mid` #555、`--light` #bbb）だけを使う。出力は16階調に丸められる。
- 細い線は潰れるので使わない。罫線や枠は 5〜6px 以上にする。
- 文字は大きめにする。主役（日付・現在気温）は極端に大きく、補助情報は `--mid` で控えめにする。
- 横置き・縦置きのどちらでも1画面に収める（`overflow: hidden` なので、はみ出すと黙って切れる）。主に使うのは横置き。
- Jinja では `{{ x.pop }}` のような書き方をすると、辞書のキーより先に dict のメソッドが解決される。表示データのキーに `pop` / `items` / `keys` / `values` / `get` などを使わない（実際に `pop` で表示が崩れたことがある）。

## Claude 使用量まわりの制約

使用量の入り方は3つある。どれも `data/claude_usage.json` に保存し、新しい値が勝つ。

1. **コンテナ内の Claude Code**（`claude.poll_loop`）
   - `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` のトークン）があるときだけ動く。
   - `claude -p` を最小構成で実行し、stream-json の `rate_limit_event.rate_limit_info.unifiedWindows` を読む。
   - `utilization` は **0〜1 の割合**、`resetsAt` は epoch 秒。保存前に0〜100へ変換している。
   - setup-token には `user:profile` の権限が無く、`/api/oauth/usage` を呼べない。そのためこの方式にしている。
   - `--bare` は OAuth を読まないので使えない。
   - 1回あたり haiku で約900トークン消費する。取得間隔を短くしすぎない。
   - Claude Code はイメージ内でバージョン固定（Dockerfile の `CLAUDE_CODE_VERSION`）、自動更新は無効。
2. **PC の statusline から POST**（`push.py --statusline`）
   - statusline JSON の `rate_limits.five_hour` / `seven_day` は、`used_percentage` が0〜100、`resets_at` が epoch 秒。
   - ユーザーの `~/.claude/settings.json` では `--passthrough ccstatusline` 付きで動いており、statusline に表示されるのは ccstatusline の出力。そのため、statusline モードでの標準出力の扱いを変えるときは注意する。
   - statusline は10秒ごとに呼ばれる。そのため送信は軽く保つ（同じ内容なら5分間は送らない、失敗後60秒は再送しない、タイムアウト1.5秒）。
3. **PC の cron から POST**（`push.py --oauth`）
   - 非公開の `/api/oauth/usage` を使う。未検証で、仕様が変わることがある。

- `rate_limits` や、その中の各枠は欠けることがある。欠けていても落ちないようにする。
- トークン類（`CLAUDE_CODE_OAUTH_TOKEN`、`CODEX_ACCESS_TOKEN`、`PUSH_TOKEN`）は `.env` にだけ書く。`docker-compose.yml` やコードに値を書かない。

## Codex 使用量まわりの制約

使用量の入り方は2つある。どちらも `data/codex_usage.json` に保存し、新しい値が勝つ。

1. **コンテナ内の Codex**（`codex.poll_loop`）
   - `codex` があり、`CODEX_ACCESS_TOKEN` か `CODEX_HOME` が設定されているときだけ動く。Docker では Dockerfile で `CODEX_HOME=/srv/data/codex` を入れている。ローカル開発では `CODEX_HOME` を設定しないので、PC の `~/.codex` のログインは勝手には使わない。
   - `CODEX_ACCESS_TOKEN`（Codex のアクセストークン）は、ChatGPT の **Business / Enterprise ワークスペース専用**。個人プラン（このPCは Free）では発行できない。
     - app-server はこの環境変数を直接は読まない（ダミーで確認済み）。そのため、起動時に `codex login --with-access-token` で `CODEX_HOME` に保存してから使う。
     - トークンの形式が不正でも、`codex login` は終了コード 0 で終わる。そのため、出力の `Error` で失敗を判定している。
     - 本物のトークンでの動作は未確認。
   - 個人プランは、コンテナ内で `codex login --device-auth` を一度実行する。NAS 専用の別ログインになるので、PC のログインには影響しない。
   - **PC の `~/.codex/auth.json` を NAS にコピーしない。** 更新用のトークンを共有するので、どちらかが更新すると、もう一方のログインが切れるおそれがある。
   - Codex CLI はイメージ内でバージョン固定（Dockerfile の `CODEX_VERSION`、GitHub の公式リリース）。バイナリは約320MBある。
2. **PC から POST**（`push_codex.py`、`--hook` は Codex の `Stop` フック用）
   - Claude の statusline と同じく、送信は軽く保つ。

- 取得は `codex app-server` の `account/rateLimits/read`（`codex app-server generate-json-schema` でスキーマを見られる）。
  - stdin を閉じると、応答の前に終了する。そのため、応答を受け取るまで stdin は開いたままにする。
  - `rateLimits.primary` / `secondary` には `usedPercent`（0〜100）、`windowDurationMins`、`resetsAt`（epoch 秒）が入る。プランによっては `secondary` が無い（Free プランは43200分＝30日の枠だけ）。
  - 応答にはアカウントIDなども入るので、使うのは `rateLimits` 部分だけにする。

## Antigravity（agy）使用量まわりの制約

- 使用量を返す公式のコマンドや API は無い。
  - `/usage` は対話画面だけ。
  - `agy -p` の stream-json に入るのはトークン数だけ（確認済み）。
  - フックの入力にもクォータは入らない。
  - 公式に読めるのは、statusline のコマンドに stdin で渡される JSON の `quota` だけ。そのため、PC の statusline から送る方式にしている（コンテナ内では取れない）。
- statusline の設定は `~/.gemini/antigravity-cli/settings.json` の `statusLine`（`type: command`、`command`、`stack_with_default` など）。状態が変わるたびに呼ばれ、5秒で打ち切られ、30回続けて失敗すると止まる。送信は軽く保つ（同じ内容なら5分は送らない、失敗後60秒は再送しない、タイムアウト1.5秒）。
- `quota` はバケットID → `{remaining_fraction（0〜1）, reset_time（ISO）, reset_in_seconds}`。
  - 実際のID（Google AI Pro）：`gemini-5h` / `gemini-weekly` / `3p-5h` / `3p-weekly`（Claude・GPT）/ `gemini-argon-wtu-weekly`。
  - IDの末尾（`5h` / `weekly`）から枠の長さを、前半から系統を決める。知らない系統も表示はする。
  - `remaining_amount` の枠（回数）と `disabled` の枠は、割合で出せないので飛ばす。
- `reset_in_seconds` は呼ぶたびに減る。そのため、重複判定には残り（0.1%単位）と `reset_time` だけを使う。
- statusline JSON には `email` も入る。送るのは `quota` と `plan_tier` だけにする。

## Kindle 側

- Kindle 側の `fetch-dashboard.sh` が `http://<NAS>:8080/dash.png` を取りに来る。HTTPS は証明書が古くて失敗しやすいので、LAN 内は HTTP にする。
- 横長PNG（`LANDSCAPE_ROTATE=0`）は、Kindle では右側が切れる（確認済み）。`eips` は画像を回転せずに縦長の画面（1072×1448）へそのまま描き、GUIを止めているので自動回転も効かない。そのため本番は `LANDSCAPE_ROTATE=90` で縦長PNGに回して出力する。
- **未確認**：`90` の向きで上下が合っているか。`90` は Kindle の下端（電源ボタン・USB）が左に来る向き。逆さまなら `270` にする。確認できたらここを更新する。

## 進め方のルール

- 次の2つは、行う前にユーザーに確認する。
  - 依存の追加（`uv add`）。イメージサイズと NAS の負荷を抑えたいため。
  - 設定の既定値（コードの `os.getenv` の既定・Dockerfile の `ENV`・`.env.example`）の変更。本番の動作に影響するため。
- CLAUDE.md、コミットメッセージ、コード内コメントは日本語で書く。
- Git：変更ごとにブランチを切り、PR で `main` にマージする。コミットとプッシュは頼まれたときだけ行う。
