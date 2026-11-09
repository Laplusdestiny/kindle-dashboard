"""PC 側で動かして、Antigravity CLI（agy）の使用量を NAS のダッシュボードサーバーへ送る。

agy の statusline から呼ぶ（stdin に statusline JSON が来る。~/.gemini/antigravity-cli/settings.json）:
  "statusLine": {"type": "command", "stack_with_default": true,
                 "command": "kindle-dash-push-agy --statusline --url http://NAS:8080"}

送るのは quota と plan_tier だけ（statusline JSON にはメールアドレスなども入っている）。
statusline は状態が変わるたびに呼ばれ、5秒で打ち切られるので、送信は軽く保つ
（同じ内容なら5分は送らない、失敗後60秒は送らない、タイムアウト1.5秒）。
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

from .push import _remember, _should_send, post

CACHE = Path.home() / ".cache" / "kindle-dash" / "last_push_agy.json"
SHORT_NAMES = {"gemini": "Gemini", "3p": "Claude/GPT"}


def pick(status: dict) -> dict:
    """statusline JSON から送る分だけを取り出す"""
    return {"quota": status.get("quota") or {}, "plan_tier": status.get("plan_tier")}


def digest(payload: dict) -> str:
    """reset_in_seconds は毎回変わるので、残り（0.1%単位）とリセット時刻だけで比べる"""
    key = {
        k: (round(float(v.get("remaining_fraction") or 0), 3), v.get("reset_time"))
        for k, v in payload["quota"].items() if isinstance(v, dict)
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def statusline_text(quota: dict) -> str:
    """statusline に出す短い表示（例: Gemini 5h 99% · Claude/GPT 5h 100%）。5時間枠の残りだけ"""
    parts = []
    for family, name in SHORT_NAMES.items():
        q = quota.get(f"{family}-5h") or {}
        if q.get("remaining_fraction") is not None:
            parts.append(f"{name} 5h {round(float(q['remaining_fraction']) * 100)}%")
    return " · ".join(parts)


def run_statusline(url: str, token: str | None, passthrough: str | None) -> None:
    raw = sys.stdin.read()
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        status = {}

    # statusline の表示は送信の成否に関係なく出す
    if passthrough:
        subprocess.run(passthrough, shell=True, input=raw, text=True)
    else:
        print(statusline_text(status.get("quota") or {}))
    sys.stdout.flush()

    payload = pick(status)
    if not payload["quota"]:
        return
    d = digest(payload)
    if not _should_send(d, CACHE):
        return
    try:
        post(url, token, payload, timeout=1.5, path="/agy-usage")
    except httpx.HTTPError:
        _remember(CACHE, failed_at=time.time())  # NAS が落ちていても statusline を止めない
        return
    _remember(CACHE, digest=d, at=time.time())


def main() -> None:
    p = argparse.ArgumentParser(description="Antigravity CLI の使用量をダッシュボードサーバーへ送る")
    p.add_argument("--url", default=(os.getenv("KINDLE_DASH_URL") or "http://localhost:8080"))
    p.add_argument("--token", default=os.getenv("KINDLE_DASH_TOKEN"), help="サーバー側 PUSH_TOKEN と同じ値")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--statusline", action="store_true", help="stdin の statusline JSON から送る")
    mode.add_argument("--file", type=Path, help="JSON ファイルの中身を送る（動作確認用）")
    p.add_argument("--passthrough", metavar="CMD", help="--statusline 時、表示はこのコマンドに任せる")
    args = p.parse_args()

    if args.statusline:
        run_statusline(args.url, args.token, args.passthrough)
        return
    post(args.url, args.token, pick(json.loads(args.file.read_text())), timeout=10, path="/agy-usage")
    print(f"sent to {args.url}")
