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
  - エンドポイント：`/dash.png`（`CACHE_SECONDS` の間キャッシュ。`?battery=82` で Kindle の電池残量も受け取る）、`/preview`（撮影前のHTML）、`GET /claude-usage`・`/codex-usage`・`/agy-usage`（保存内容の確認用）、`/health`。
- `claude.py` … Claude 使用量を受け取り（`normalize`）、`data/claude_usage.json` へ保存し、表示用に整形する（`view`）。
- `train.py` … 阪急の駅時刻表ページを1日1回取得して解析し（`parse_timetable`）、`data/train/` に保存する。平日・土休日は内閣府の祝日CSVで判定し、`TRAIN_WINDOW_MINUTES` 分以内の発車を返す（`view`）。
  - 電車パネルを出すと `body.with-train` が付き、天気などの余白を詰めて1画面に収める。縦置きでは、3時間ごとの予報と3日分の予報を隠している。
  - AI の使用量は、1行1サービスの表（`.usage`）にしている。列は「5時間」「週間」。Antigravity は系統（Gemini・Claude/GPT・Argon）ごとに行を分け、見出し行を1つ置く。サービスや枠が増えると行が増えるので、追加したら横置き・縦置きの両方で収まるかを見る。
- `codex.py` … Codex 使用量を `codex app-server` で定期取得し（`fetch_rate_limits` / `poll_loop`）、`data/codex_usage.json` へ保存する。
- `agy.py` … Antigravity CLI の使用量を `agy -p /usage --output-format json` で定期取得し（`fetch_usage` / `poll_loop`）、`data/agy_usage.json` へ保存する。
- `usage.py` … Claude・Codex・Antigravity の保存データから、使用量パネルの表（`rows`）を作る。どの枠を「5時間」「週間」のどちらの列に入れるかは、枠の長さ（分）で決める。
  - 保存・読み込みは `claude.py` の `write_json` / `read_json` を共用している。
- `kindle.py` … `/dash.png?battery=` で届いた Kindle の電池残量を `data/kindle.json` に保存し、フッター右に出す（6時間より古ければ出さない）。
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
使用量パネルは、データがある状態で見る。`examples/*.json` を各モジュールの `save()` で `data/` に入れてから描画する（README の「表示の確認」を参照）。

確認用に起動したサーバーは PID を指定して止める（`ss -ltnp` で PID を調べる）。`pkill -f` は、パターンが自分のシェルのコマンドラインにも一致して、シェルごと落ちる。

## E-Ink のデザインルール

- 白地・墨（#000）・灰2段階（`--mid` #555、`--light` #bbb）だけを使う。出力は16階調に丸められる。
- 細い線は潰れるので使わない。罫線や枠は 5〜6px 以上にする。
- 文字は大きめにする。主役（日付・現在気温）は極端に大きく、補助情報は `--mid` で控えめにする。
- 横置き・縦置きのどちらでも1画面に収める（`overflow: hidden` なので、はみ出すと黙って切れる）。主に使うのは横置き。
- Jinja では `{{ x.pop }}` のような書き方をすると、辞書のキーより先に dict のメソッドが解決される。表示データのキーに `pop` / `items` / `keys` / `values` / `get` などを使わない（実際に `pop` で表示が崩れたことがある）。

## 使用量の取得まわりの共通ルール

- **使用量はコンテナ内で取得する。PC など別のマシンから送ってもらう機能は持たない**（ユーザーの判断で削除した。POST エンドポイントも無い）。
- 各サービスの `poll_loop` は、起動直後に1回、その後 `*_POLL_MINUTES`（既定15分）ごとに取得する。失敗してもループは止めず、同じ失敗のログは1回だけ出す。
- トークン類（`CLAUDE_CODE_OAUTH_TOKEN`、`CODEX_ACCESS_TOKEN`）は `.env` にだけ書く。`docker-compose.yml` やコードに値を書かない。
- ログイン情報（Codex・agy）は `./data` の下に置き、再起動しても残るようにする。**PC のログイン情報を NAS にコピーしない**（NAS では別にログインする）。

## Claude 使用量まわりの制約

- `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` のトークン）があるときだけ動く（`claude.poll_loop`）。
- `claude -p` を最小構成で実行し、stream-json の `rate_limit_event.rate_limit_info.unifiedWindows` を読む。
  - `utilization` は **0〜1 の割合**、`resetsAt` は epoch 秒。保存前に0〜100へ変換している。
  - `five_hour` / `seven_day` は欠けることがある。欠けていても落ちないようにする。
- setup-token には `user:profile` の権限が無く、`/api/oauth/usage` を呼べない。そのためこの方式にしている。
- `--bare` は OAuth を読まないので使えない。
- 1回あたり haiku で約900トークン消費する。取得間隔を短くしすぎない。
- Claude Code はイメージ内でバージョン固定（Dockerfile の `CLAUDE_CODE_VERSION`）、自動更新は無効。

## Codex 使用量まわりの制約

- `codex` があり、`CODEX_ACCESS_TOKEN` か `CODEX_HOME` が設定されているときだけ動く（`codex.poll_loop`）。Docker では Dockerfile で `CODEX_HOME=/srv/data/codex` を入れている。ローカル開発では `CODEX_HOME` を設定しないので、PC の `~/.codex` のログインは勝手には使わない。
- `CODEX_ACCESS_TOKEN`（Codex のアクセストークン）は、ChatGPT の **Business / Enterprise ワークスペース専用**。個人プラン（このPCは Free）では発行できない。
  - app-server はこの環境変数を直接は読まない（ダミーで確認済み）。そのため、起動時に `codex login --with-access-token` で `CODEX_HOME` に保存してから使う。
  - トークンの形式が不正でも、`codex login` は終了コード 0 で終わる。そのため、出力の `Error` で失敗を判定している。
  - 本物のトークンでの動作は未確認。
- 個人プランは、コンテナ内で `codex login --device-auth` を一度実行する。NAS 専用の別ログインになるので、PC のログインには影響しない。
- PC の `~/.codex/auth.json` をコピーすると、更新用のトークンを共有することになり、どちらかが更新するともう一方のログインが切れるおそれがある。
- Codex CLI はイメージ内でバージョン固定（Dockerfile の `CODEX_VERSION`、GitHub の公式リリース）。バイナリは約320MBある。
- 取得は `codex app-server` の `account/rateLimits/read`（`codex app-server generate-json-schema` でスキーマを見られる）。
  - stdin を閉じると、応答の前に終了する。そのため、応答を受け取るまで stdin は開いたままにする。
  - `rateLimits.primary` / `secondary` には `usedPercent`（0〜100）、`windowDurationMins`、`resetsAt`（epoch 秒）が入る。プランによっては `secondary` が無い（Free プランは43200分＝30日の枠だけ）。
  - 応答にはアカウントIDなども入るので、使うのは `rateLimits` 部分だけにする。

## Antigravity（agy）使用量まわりの制約

- `agy -p /usage --output-format json` で、モデルを呼ばずに（トークン 0 で）クォータが返る（agy 1.5.3 で確認）。
  - 公式ドキュメントでは、print モードのスラッシュコマンドは「テキストの報告を出す」とだけ書かれている。JSON の形（`command.data.groups[].buckets[]`）は、実際の出力で確認したもの。
  - 普通のプロンプトの `-p` 出力にはトークン数しか入らない。フックの入力にもクォータは入らない。
- バケットは `{id, window（5h / weekly）, remaining_fraction（0〜1）, reset_time（ISO）}`。
  - 実際のID（Google AI Pro）：`gemini-5h` / `gemini-weekly` / `3p-5h` / `3p-weekly`（Claude・GPT）/ `gemini-argon-wtu-weekly`。
  - 枠の長さは `window`（無ければIDの末尾）から、系統はIDの前半から決める。知らない系統も表示はする。
  - `remaining_fraction` の無い枠（回数で数える枠など）と `disabled` の枠は、割合で出せないので飛ばす。
- 未ログインだと `agy -p` は Google のログインを始め、60秒待ってから `{"status": "ERROR", "error": "authentication failed or timed out"}` を返す（終了コード 1）。取得は別スレッドで動くので、描画は止まらない。
- ログインはコンテナ内で `docker compose exec -it kindle-dash agy -p /usage` を実行し、URLをブラウザで開いて得たコードを貼る。
  - ログイン情報は `~/.gemini` に入るので、Dockerfile で `/root/.gemini` を `/srv/data/agy` へのシンボリックリンクにしている（`AGY_HOME_DIR` は、そのディレクトリを作るためだけに使う）。
  - コンテナ内での実際のログインは未確認。OS のキーリングが無い環境では、ファイルに保存されると見ている。
- agy は公式インストーラと同じ配布元（`storage.googleapis.com/antigravity-public/antigravity-cli/<版>-<ビルド>/...`）から、Dockerfile の `AGY_BUILD` で固定して入れている。最新のビルド番号は、配布元のマニフェスト（`.../manifests/linux_amd64.json`）の `url` で分かる。
- agy は通常の実行中に自分で更新する仕組みを持っていて、止める設定は公開されていない。
- ローカル開発でも、PC に agy があれば取得する（PC の agy のログインで `/usage` を読むだけで、トークンは消費しない）。止めたいときは `AGY_POLL_MINUTES=0`。

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
