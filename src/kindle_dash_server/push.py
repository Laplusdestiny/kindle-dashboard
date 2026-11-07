"""PC 側で動かして、Claude の使用量を NAS のダッシュボードサーバーへ送る。

  # Claude Code の statusline から（stdin に statusline JSON が来る）
  kindle-dash-push-claude --statusline --url http://NAS:8080
  # 既存の statusline（例: ccstatusline）の表示はそのまま使う
  kindle-dash-push-claude --statusline --passthrough ccstatusline --url http://NAS:8080

  # cron などから定期実行（Claude Code のログイン情報を読んで使用量APIを叩く）
  kindle-dash-push-claude --oauth --url http://NAS:8080

URL とトークンは環境変数 KINDLE_DASH_URL / KINDLE_DASH_TOKEN でも指定できる。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

USAGE_API = "https://api.anthropic.com/api/oauth/usage"
CACHE = Path.home() / ".cache" / "kindle-dash" / "last_push.json"
# statusline は頻繁に呼ばれるので、同じ内容なら一定時間は送らない
RESEND_SECONDS = 300
# 送信に失敗したら、この秒数は再送しない
RETRY_SECONDS = 60


def post(url: str, token: str | None, payload: dict, timeout: float) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.post(f"{url.rstrip('/')}/claude-usage", json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()


def fetch_oauth_usage(credentials: Path) -> dict:
    # Claude Code がログイン時に保存したアクセストークンを読むだけ（更新はしない）
    token = json.loads(credentials.read_text())["claudeAiOauth"]["accessToken"]
    r = httpx.get(
        USAGE_API,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def statusline_text(rl: dict) -> str:
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "週")):
        w = rl.get(key) or {}
        if w.get("used_percentage") is not None:
            parts.append(f"{label} {round(w['used_percentage'])}%")
    return " · ".join(parts)


def _should_send(digest: str) -> bool:
    try:
        last = json.loads(CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return True
    now = time.time()
    if now - last.get("failed_at", 0) < RETRY_SECONDS:
        return False  # 直前に送れなかったので、少し待つ（statusline を毎回遅くしない）
    return not (last.get("digest") == digest and now - last.get("at", 0) < RESEND_SECONDS)


def _remember(**kw) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(kw))


def run_statusline(url: str, token: str | None, passthrough: str | None) -> None:
    raw = sys.stdin.read()
    try:
        rl = json.loads(raw).get("rate_limits") or {}
    except json.JSONDecodeError:
        rl = {}

    # statusline の表示は送信の成否に関係なく出す
    if passthrough:
        # 既存の statusline コマンドに同じ JSON を渡し、その出力をそのまま表示する
        subprocess.run(passthrough, shell=True, input=raw, text=True)
    else:
        print(statusline_text(rl))
    sys.stdout.flush()

    if not rl:
        return
    digest = hashlib.sha256(json.dumps(rl, sort_keys=True).encode()).hexdigest()
    if not _should_send(digest):
        return
    try:
        post(url, token, {"rate_limits": rl}, timeout=1.5)
    except httpx.HTTPError:
        _remember(failed_at=time.time())  # NAS が落ちていても statusline を止めない
        return
    _remember(digest=digest, at=time.time())


def main() -> None:
    p = argparse.ArgumentParser(description="Claude の使用量をダッシュボードサーバーへ送る")
    p.add_argument("--url", default=os.getenv("KINDLE_DASH_URL", "http://localhost:8080"))
    p.add_argument("--token", default=os.getenv("KINDLE_DASH_TOKEN"), help="サーバー側 PUSH_TOKEN と同じ値")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--statusline", action="store_true", help="stdin の statusline JSON から送る")
    mode.add_argument("--oauth", action="store_true", help="使用量APIから取得して送る")
    mode.add_argument("--file", type=Path, help="JSON ファイルの中身をそのまま送る（動作確認用）")
    p.add_argument("--passthrough", metavar="CMD", help="--statusline 時、表示はこのコマンド（例: ccstatusline）に任せる")
    p.add_argument(
        "--credentials",
        type=Path,
        default=Path(os.getenv("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / ".credentials.json",
    )
    args = p.parse_args()

    if args.statusline:
        run_statusline(args.url, args.token, args.passthrough)
        return
    payload = fetch_oauth_usage(args.credentials) if args.oauth else json.loads(args.file.read_text())
    post(args.url, args.token, payload, timeout=10)
    print(f"sent to {args.url}")
