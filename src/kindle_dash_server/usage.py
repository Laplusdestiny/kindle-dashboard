"""AI の使用量パネル（Claude・Codex・Antigravity）を、1行1サービスの表にまとめる。

列は「短い枠（1日以下。5時間など）」と「長い枠（週間・30日など）」の2つ。
5時間・週間以外の長さの枠（Codex Free の30日など）は、セルに枠の名前を添える。
"""
from __future__ import annotations

import os
from datetime import datetime

from . import agy, claude, codex
from .claude import _parse_time
from .codex import window_label

HEADERS = ("5時間", "週間")
STANDARD_MINUTES = {300: 0, 7 * 24 * 60: 1}
CLAUDE_MINUTES = {"five_hour": 300, "seven_day": 7 * 24 * 60}


def _short_time(dt: datetime, now: datetime) -> str:
    hm = f"{dt.hour}:{dt.minute:02d}"
    return hm if dt.date() == now.date() else f"{dt.month}/{dt.day} {hm}"


def _cell(w: dict, minutes: int | None, now: datetime) -> dict:
    note = "" if minutes in STANDARD_MINUTES or not minutes else f"{window_label(minutes)}枠"
    reset = _parse_time(w.get("resets_at"))
    if reset and reset <= now:
        # 受信後にリセット時刻を過ぎていれば、枠は空いているとみなす
        return {"used": 0, "left": 100, "reset": "リセット済", "note": note}
    used = max(0.0, min(100.0, float(w["used"])))
    return {
        "used": round(used),
        "left": round(100 - used),
        "reset": _short_time(reset, now) if reset else "",
        "note": note,
    }


def _row(name: str, sub: str, windows: list[tuple[dict, int | None]], now: datetime) -> dict:
    cells: list[dict | None] = [None, None]
    for w, minutes in windows:
        slot = 0 if (minutes and minutes <= 24 * 60) else 1
        if cells[slot] is None:
            cells[slot] = _cell(w, minutes, now)
    return {"name": name, "sub": sub, "cells": cells}


def _received(data: dict, now: datetime) -> str:
    at = _parse_time(data.get("received_at"))
    return f"{_short_time(at, now)} 時点" if at else ""


def rows(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now().astimezone()
    show = lambda key: (os.getenv(key) or "1") != "0"  # noqa: E731
    out = []
    if show("SHOW_CLAUDE"):
        data = claude.load()
        if data:
            ws = [(w, CLAUDE_MINUTES.get(k)) for k, w in data["windows"].items()]
            out.append(_row("Claude", _received(data, now), ws, now))
        else:
            out.append(_row("Claude", "未受信", [], now))
    if show("SHOW_CODEX"):
        data = codex.load()
        if data:
            ws = [(w, w.get("minutes")) for w in data["windows"].values()]
            out.append(_row("Codex", _received(data, now), ws, now))
        else:
            out.append(_row("Codex", "未受信", [], now))
    if show("SHOW_AGY"):
        data = agy.load()
        groups = agy.families(data)
        if groups:
            # Antigravity は系統（Gemini・Claude/GPT など）ごとに行を分けるので、見出し行を1つ置く
            sub = "・".join(x for x in ((data.get("plan") or ""), _received(data, now)) if x)
            out.append({"group": "Antigravity", "sub": sub})
            for name, buckets in groups:
                ws = [(b, b.get("minutes")) for b in buckets.values()]
                out.append(_row(name, "", ws, now))
        else:
            out.append(_row("Antigravity", "未受信", [], now))
    return out
