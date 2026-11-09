"""PC 側で動かして、Codex の使用量（ChatGPT プランの残り）を NAS のダッシュボードサーバーへ送る。

  # 手動・cron から
  kindle-dash-push-codex --url http://NAS:8080
  # Codex の Stop フック（ターンが終わるたび）から。stdin のフック JSON は読み捨て、何も出力しない
  kindle-dash-push-codex --hook --url http://NAS:8080

使用量は `codex app-server` の account/rateLimits/read で読む（Codex 公式のプロトコル。
トークンは消費しない）。送るのは rateLimits 部分だけで、アカウントIDなどは送らない。
URL とトークンは環境変数 KINDLE_DASH_URL / KINDLE_DASH_TOKEN でも指定できる。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

from .codex import fetch_rate_limits
from .push import _remember, _should_send, post

CACHE = Path.home() / ".cache" / "kindle-dash" / "last_push_codex.json"


def main() -> None:
    p = argparse.ArgumentParser(description="Codex の使用量をダッシュボードサーバーへ送る")
    p.add_argument("--url", default=(os.getenv("KINDLE_DASH_URL") or "http://localhost:8080"))
    p.add_argument("--token", default=os.getenv("KINDLE_DASH_TOKEN"), help="サーバー側 PUSH_TOKEN と同じ値")
    p.add_argument("--hook", action="store_true", help="Codex のフックから呼ぶ（何も出力せず、失敗しても 0 で終わる）")
    p.add_argument("--file", type=Path, help="JSON ファイルの中身をそのまま送る（動作確認用）")
    args = p.parse_args()

    if not args.hook:
        rl = json.loads(args.file.read_text()) if args.file else fetch_rate_limits()
        post(args.url, args.token, {"rateLimits": rl}, timeout=10, path="/codex-usage")
        print(f"sent to {args.url}")
        return

    # フックから: Codex を待たせないよう、同じ内容は5分送らず、失敗後60秒は試さない
    if not sys.stdin.isatty():
        sys.stdin.read()
    if not _should_send("", CACHE):
        return  # 直前に失敗した（digest "" は実データと一致しないので、失敗後の待機だけを見る）
    try:
        rl = fetch_rate_limits(timeout=8)
        digest = hashlib.sha256(json.dumps(rl, sort_keys=True).encode()).hexdigest()
        if _should_send(digest, CACHE):
            post(args.url, args.token, {"rateLimits": rl}, timeout=2, path="/codex-usage")
            _remember(CACHE, digest=digest, at=time.time())
    except (RuntimeError, OSError, httpx.HTTPError):
        _remember(CACHE, failed_at=time.time())
