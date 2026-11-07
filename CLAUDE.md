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
  - エンドポイント：`/dash.png`（`CACHE_SECONDS` の間キャッシュ）、`/preview`（撮影前のHTML）、`POST /claude-usage`、`GET /claude-usage`、`/health`。
- `claude.py` … Claude 使用量を受け取り（`normalize`）、`data/claude_usage.json` へ保存し、表示用に整形する（`view`）。
- `train.py` … 阪急の駅時刻表ページを1日1回取得して解析し（`parse_timetable`）、`data/train/` に保存する。平日・土休日は内閣府の祝日CSVで判定し、`TRAIN_WINDOW_MINUTES` 分以内の発車を返す（`view`）。
  - 電車パネルを出すと `body.with-train` が付き、天気などの余白を詰めて1画面に収める。縦置きでは Claude の見出しを隠している。
- `push.py` … PC 側で動かす送信コマンド `kindle-dash-push-claude`。`--statusline`、`--oauth`、`--file` の3方式。
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
- トークン類（`CLAUDE_CODE_OAUTH_TOKEN`、`PUSH_TOKEN`）は `.env` にだけ書く。`docker-compose.yml` やコードに値を書かない。

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
