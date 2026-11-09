"""Claude のプラン使用量（5時間枠・週間枠）を集めて保存し、表示用に整える。

使用量の入り方は2通り（どちらも data/claude_usage.json に保存し、新しい方が勝つ）:
  1. PC から POST /claude-usage で送ってもらう（kindle-dash-push-claude）
  2. コンテナ内の Claude Code を CLAUDE_CODE_OAUTH_TOKEN で定期実行し、
     stream-json に出る rate_limit_event から読む（poll_loop）

POST で受け付ける JSON は次のどれでもよい:
  - Claude Code の statusline に渡される JSON 全体（rate_limits を含む）
  - その rate_limits 部分だけ
  - /api/oauth/usage の応答（five_hour.utilization など）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

WINDOWS = {"five_hour": "5時間", "seven_day": "週間"}


def data_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "claude_usage.json"


def _parse_time(v) -> datetime | None:
    """epoch秒・epochミリ秒・ISO文字列のどれでも受ける。返り値はローカル時刻（aware）"""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e12 else v).astimezone()
    s = str(v).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return (dt if dt.tzinfo else dt.astimezone()).astimezone()


def from_rate_limit_event(event: dict) -> dict | None:
    """stream-json の rate_limit_event を statusline と同じ形（0〜100%）に直す。

    unifiedWindows の utilization は 0〜1 の割合、resetsAt は epoch 秒。
    """
    windows = ((event.get("rate_limit_info") or {}).get("unifiedWindows")) or {}
    out = {}
    for key in WINDOWS:
        w = windows.get(key)
        if isinstance(w, dict) and w.get("utilization") is not None:
            out[key] = {"used_percentage": float(w["utilization"]) * 100, "resets_at": w.get("resetsAt")}
    return {"rate_limits": out} if out else None


def normalize(payload: dict, source: str = "push") -> dict:
    src = payload.get("rate_limits", payload) if isinstance(payload, dict) else None
    if not isinstance(src, dict):
        raise ValueError("JSON オブジェクトではありません")
    windows = {}
    for key in WINDOWS:
        w = src.get(key)
        if not isinstance(w, dict):
            continue
        used = w.get("used_percentage", w.get("utilization"))
        if used is None:
            continue
        reset = _parse_time(w.get("resets_at"))
        windows[key] = {"used": float(used), "resets_at": reset.isoformat() if reset else None}
    if not windows:
        raise ValueError("five_hour / seven_day の使用率が見つかりません")
    return {"windows": windows, "received_at": datetime.now().astimezone().isoformat(), "source": source}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 書きかけを読まれないように置き換えで保存


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(payload: dict, source: str = "push") -> dict:
    data = normalize(payload, source)
    write_json(data_file(), data)
    return data


def load() -> dict | None:
    return read_json(data_file())


# ---- コンテナ内の Claude Code から取得する ----

# 使用量を読むためだけの最小の1往復。ツール・設定・MCP を切り、軽いモデルで「OK」とだけ返させる
CLAUDE_ARGS = [
    "-p", "ok",
    "--output-format", "stream-json", "--verbose",
    "--model", (os.getenv("CLAUDE_POLL_MODEL") or "haiku"),
    "--tools", "",
    "--system-prompt", "Reply with OK only.",
    "--strict-mcp-config",
    "--setting-sources", "",
    "--disable-slash-commands",
    "--no-session-persistence",
]


def poll_enabled() -> bool:
    return bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN")) and poll_minutes() > 0


def poll_minutes() -> float:
    return float(os.getenv("CLAUDE_POLL_MINUTES") or "15")


async def poll_once(timeout: float = 120) -> dict | None:
    """claude -p を1回動かし、rate_limit_event が出れば保存して返す"""
    exe = shutil.which("claude")
    if not exe:
        log.warning("claude コマンドが見つかりません")
        return None
    with tempfile.TemporaryDirectory() as cwd:  # CLAUDE.md などを拾わない空ディレクトリで動かす
        proc = await asyncio.create_subprocess_exec(
            exe, *CLAUDE_ARGS, cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("claude が %s 秒で終わりませんでした", timeout)
            return None
    payload, result = None, None
    for line in out.decode(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "rate_limit_event":
            payload = from_rate_limit_event(event) or payload  # 複数出たら最後のものを使う
        elif event.get("type") == "result":
            result = event
    if payload is None:
        # 認証エラーなどは result イベントの result 欄（なければ stderr）に出る
        reason = (result or {}).get("result") or err.decode(errors="replace")[-300:]
        log.warning("使用量を取得できませんでした（exit %s）: %s", proc.returncode, reason)
        return None
    data = save(payload, source="claude-code")
    log.info("Claude 使用量を更新: %s", {k: round(v["used"]) for k, v in data["windows"].items()})
    return data


async def poll_loop(on_update=None) -> None:
    """起動直後に1回、その後 CLAUDE_POLL_MINUTES ごとに取得する"""
    while True:
        try:
            if await poll_once() and on_update:
                on_update()
        except Exception:  # ループ自体は止めない
            log.exception("Claude 使用量の取得に失敗")
        await asyncio.sleep(poll_minutes() * 60)
