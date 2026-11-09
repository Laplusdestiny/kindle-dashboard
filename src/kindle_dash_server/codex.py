"""Codex（ChatGPT プラン）の使用量を集めて保存し、表示用に整える。

使用量は `codex app-server` の account/rateLimits/read（Codex 公式のプロトコル。トークンは消費しない）で読む。
入り方は2通り（どちらも data/codex_usage.json に保存し、新しい方が勝つ）:
  1. コンテナ内の Codex で定期取得する（poll_loop）。ログインは次のどちらか:
     - CODEX_ACCESS_TOKEN（ChatGPT の Business / Enterprise ワークスペースで発行するアクセストークン）。
       起動時に `codex login --with-access-token` で CODEX_HOME に保存してから使う
     - 個人プランは、コンテナ内で一度 `codex login --device-auth` を実行する。
       CODEX_HOME（Docker では ./data/codex）に保存され、PC とは別のログインになる
  2. PC から POST /codex-usage で送ってもらう（kindle-dash-push-codex）

POST で受け付ける JSON は次のどれでもよい:
  - account/rateLimits/read の result 全体（rateLimits を含む）
  - その rateLimits 部分だけ（primary / secondary）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .claude import _parse_time, read_json, write_json

log = logging.getLogger(__name__)

KEYS = ("primary", "secondary")


def data_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "codex_usage.json"


def window_label(minutes) -> str:
    """windowDurationMins から表示名を作る（300→5時間、10080→週間、43200→30日）"""
    if not minutes:
        return "—"
    minutes = int(minutes)
    if minutes == 7 * 24 * 60:
        return "週間"
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)}日"
    if minutes % 60 == 0:
        return f"{minutes // 60}時間"
    return f"{minutes}分"


def normalize(payload: dict, source: str = "push") -> dict:
    if not isinstance(payload, dict):
        raise ValueError("JSON オブジェクトではありません")
    src = payload.get("rateLimits", payload)
    if not isinstance(src, dict):
        raise ValueError("rateLimits がオブジェクトではありません")
    windows = {}
    for key in KEYS:
        w = src.get(key)
        if not isinstance(w, dict) or w.get("usedPercent") is None:
            continue  # プランによっては secondary が無い
        reset = _parse_time(w.get("resetsAt"))
        windows[key] = {
            "label": window_label(w.get("windowDurationMins")),
            "minutes": w.get("windowDurationMins"),
            "used": float(w["usedPercent"]),
            "resets_at": reset.isoformat() if reset else None,
        }
    if not windows:
        raise ValueError("primary / secondary の使用率が見つかりません")
    return {
        "windows": windows,
        "plan": src.get("planType"),
        "received_at": datetime.now().astimezone().isoformat(),
        "source": source,
    }


def save(payload: dict, source: str = "push") -> dict:
    data = normalize(payload, source)
    write_json(data_file(), data)
    return data


def load() -> dict | None:
    return read_json(data_file())


# ---- codex app-server から読む（PC 側の送信コマンドでも使う）----

def fetch_rate_limits(timeout: float = 20) -> dict:
    """codex app-server と JSON-RPC（1行1メッセージ）で話して rateLimits を返す。

    応答にはアカウントIDなども入るので、返すのは rateLimits 部分だけにする。
    """
    exe = shutil.which("codex")
    if not exe:
        raise RuntimeError("codex コマンドが見つかりません")
    proc = subprocess.Popen(
        [exe, "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        for msg in (
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "kindle-dash", "version": "0.1"}}},
            {"method": "initialized"},
            {"id": 2, "method": "account/rateLimits/read"},
        ):
            proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()  # stdin は閉じない（閉じると応答前に終了する）
        deadline = time.monotonic() + timeout
        for line in proc.stdout:
            if time.monotonic() > deadline:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != 2:
                continue  # initialize の応答や通知
            if "error" in msg:
                raise RuntimeError((msg["error"] or {}).get("message") or str(msg["error"]))
            return msg["result"]["rateLimits"]
        raise RuntimeError("codex app-server から応答がありませんでした")
    finally:
        proc.kill()
        proc.wait()


# ---- コンテナ内の Codex から定期取得する ----

def poll_minutes() -> float:
    return float(os.getenv("CODEX_POLL_MINUTES") or "15")


def poll_enabled() -> bool:
    """codex があり、トークンか CODEX_HOME（Docker では Dockerfile で設定）が指定されているときだけ動かす。

    ローカル開発では CODEX_HOME を設定しないので、PC の ~/.codex のログインを勝手には使わない。
    """
    if not shutil.which("codex") or poll_minutes() <= 0:
        return False
    return bool(os.getenv("CODEX_ACCESS_TOKEN") or os.getenv("CODEX_HOME"))


def login_with_token() -> None:
    """CODEX_ACCESS_TOKEN で CODEX_HOME にログインする（app-server は環境変数を直接は読まないため）"""
    token = os.getenv("CODEX_ACCESS_TOKEN")
    if not token:
        return
    r = subprocess.run(
        ["codex", "login", "--with-access-token"],
        input=token, capture_output=True, text=True, timeout=60,
    )
    out = (r.stdout + r.stderr).strip().splitlines()
    # 失敗しても終了コードが 0 のことがあるので、出力の文言でも判定する
    if r.returncode != 0 or any("Error" in line for line in out):
        log.warning("CODEX_ACCESS_TOKEN でログインできませんでした: %s", out[-1] if out else r.returncode)
    else:
        log.info("CODEX_ACCESS_TOKEN でログインしました")


async def poll_loop(on_update=None) -> None:
    """起動直後に1回、その後 CODEX_POLL_MINUTES ごとに取得する"""
    home = os.getenv("CODEX_HOME")
    if home:
        Path(home).mkdir(parents=True, exist_ok=True)  # 無いと codex が起動しない
    await asyncio.to_thread(login_with_token)
    last_error = None
    while True:
        try:
            rl = await asyncio.to_thread(fetch_rate_limits, 60)
            data = save({"rateLimits": rl}, source="codex")
            log.info("Codex 使用量を更新: %s", {v["label"]: round(v["used"]) for v in data["windows"].values()})
            last_error = None
            if on_update:
                on_update()
        except Exception as e:  # ループ自体は止めない
            msg = str(e)
            if msg != last_error:  # 同じ失敗を15分ごとに出し続けない
                hint = ""
                if "authentication required" in msg:
                    hint = "（docker compose exec kindle-dash codex login --device-auth でログインするか、CODEX_ACCESS_TOKEN を設定してください）"
                log.warning("Codex 使用量を取得できませんでした: %s%s", msg, hint)
                last_error = msg
        await asyncio.sleep(poll_minutes() * 60)
