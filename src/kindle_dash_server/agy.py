"""Antigravity CLI（agy）の使用量（クォータ）を定期取得して保存する。

`agy -p /usage --output-format json` を実行すると、モデルを呼ばずに（トークン 0 で）
`command.data.groups[].buckets[]` にクォータが返る（agy 1.5.3 で確認）:
  {"id": "gemini-5h", "window": "5h", "remaining_fraction": 0.99, "reset_time": "2026-...Z"}
バケットIDは実際には次のようなもの（Google AI Pro で確認）:
  gemini-5h / gemini-weekly                 … Gemini のモデル
  3p-5h / 3p-weekly                         … Claude・GPT のモデル（third party。表示は Claude/GPT）
  gemini-argon-wtu-weekly                   … Gemini 4 Argon（表示は Argon）

ログインはコンテナ内で一度だけ対話的に行う（docker compose exec -it kindle-dash agy -p /usage を実行し、
表示されたURLをブラウザで開いて得たコードを貼る）。未ログインのまま -p を実行すると、60秒待ってから失敗する。
Docker では ~/.gemini を ./data/agy に向けているので、再起動してもログインが残る。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .claude import _parse_time, read_json, write_json

log = logging.getLogger(__name__)

# 枠の種類 → 長さ（分）
WINDOW_MINUTES = {"5h": 300, "daily": 24 * 60, "weekly": 7 * 24 * 60}
# バケットIDの前半（モデルの系統）→ 表示名
FAMILY_NAMES = {"gemini": "Gemini", "3p": "Claude/GPT", "gemini-argon-wtu": "Argon"}


def data_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "agy_usage.json"


def split_bucket(bucket_id: str, window: str | None = None) -> tuple[str, int | None]:
    """('gemini-weekly', 'weekly') → ('gemini', 10080)。枠の種類はバケットIDの末尾からも読む"""
    family, _, suffix = bucket_id.rpartition("-")
    if not family or suffix not in WINDOW_MINUTES:
        family = bucket_id
    return family, WINDOW_MINUTES.get(window or suffix)


def family_name(family: str) -> str:
    if family in FAMILY_NAMES:
        return FAMILY_NAMES[family]
    # 知らない系統は、短い記号を除いて頭を大文字にする（例: gemini-foo-x → Gemini Foo）
    words = [w.capitalize() for w in family.split("-") if len(w) > 3]
    return " ".join(words) or family


def normalize(result: dict, source: str = "agy") -> dict:
    """`agy -p /usage --output-format json` の出力を保存用の形にする"""
    groups = (((result or {}).get("command") or {}).get("data") or {}).get("groups")
    if not isinstance(groups, list):
        raise ValueError("command.data.groups が見つかりません")
    buckets = {}
    for g in groups:
        for b in (g or {}).get("buckets") or []:
            if not isinstance(b, dict) or b.get("disabled") or b.get("remaining_fraction") is None or not b.get("id"):
                continue  # 回数で数える枠や無効な枠は、割合で出せないので飛ばす
            family, minutes = split_bucket(b["id"], b.get("window"))
            reset = _parse_time(b.get("reset_time"))
            buckets[b["id"]] = {
                "family": family,
                "minutes": minutes,
                "used": (1 - float(b["remaining_fraction"])) * 100,
                "resets_at": reset.isoformat() if reset else None,
            }
    if not buckets:
        raise ValueError("remaining_fraction のある枠が見つかりません")
    return {"buckets": buckets, "received_at": datetime.now().astimezone().isoformat(), "source": source}


def save(result: dict, source: str = "agy") -> dict:
    data = normalize(result, source)
    write_json(data_file(), data)
    return data


def load() -> dict | None:
    return read_json(data_file())


def families(data: dict | None) -> list[tuple[str, dict]]:
    """表示用に、系統ごとの枠をまとめる: [(表示名, {bucket_id: 枠}), ...]。Gemini → Claude/GPT → その他の順"""
    if not data:
        return []
    groups: dict[str, dict] = {}
    for bucket_id, b in data["buckets"].items():
        groups.setdefault(b["family"], {})[bucket_id] = b
    order = list(FAMILY_NAMES) + sorted(f for f in groups if f not in FAMILY_NAMES)
    return [(family_name(f), groups[f]) for f in order if f in groups]


# ---- agy で定期取得する ----

def poll_minutes() -> float:
    return float(os.getenv("AGY_POLL_MINUTES") or "15")


def poll_enabled() -> bool:
    return bool(shutil.which("agy")) and poll_minutes() > 0


def fetch_usage(timeout: float = 90) -> dict:
    """`agy -p /usage` を実行して JSON を返す。CLAUDE.md などを拾わないよう空ディレクトリで動かす"""
    exe = shutil.which("agy")
    if not exe:
        raise RuntimeError("agy コマンドが見つかりません")
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run(
            [exe, "-p", "/usage", "--output-format", "json"],
            cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        )
    try:
        result = json.loads(r.stdout)
    except json.JSONDecodeError:
        # 未ログインなどは JSON にならず、stderr に理由が出る
        reason = (r.stderr.strip() or r.stdout.strip())[-300:]
        raise RuntimeError(f"exit {r.returncode}: {reason}") from None
    if result.get("status") != "SUCCESS":
        # 未ログインだと Google のログインを始めて60秒待ち、error に "authentication failed or timed out" が入る
        reason = result.get("error") or result.get("response") or ""
        raise RuntimeError(f"status {result.get('status')}: {str(reason)[-300:]}")
    return result


async def poll_loop(on_update=None) -> None:
    """起動直後に1回、その後 AGY_POLL_MINUTES ごとに取得する"""
    home = os.getenv("AGY_HOME_DIR")
    if home:
        Path(home).mkdir(parents=True, exist_ok=True)  # ~/.gemini の向け先（Docker のみ）
    last_error = None
    while True:
        try:
            data = save(await asyncio.to_thread(fetch_usage))
            log.info("Antigravity 使用量を更新: %s", {k: round(v["used"]) for k, v in data["buckets"].items()})
            last_error = None
            if on_update:
                on_update()
        except Exception as e:  # ループ自体は止めない
            msg = str(e)
            if msg != last_error:  # 同じ失敗を15分ごとに出し続けない
                hint = ""
                if "auth" in msg.lower() or "sign in" in msg.lower() or "login" in msg.lower():
                    hint = "（docker compose exec -it kindle-dash agy -p /usage を実行し、表示されたURLで得たコードを貼ってログインしてください）"
                log.warning("Antigravity 使用量を取得できませんでした: %s%s", msg, hint)
                last_error = msg
        await asyncio.sleep(poll_minutes() * 60)
