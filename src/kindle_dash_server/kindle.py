"""Kindle 本体の状態（電池残量）。

Kindle 側の fetch-dashboard.sh が `/dash.png?battery=82` のように付けて取りに来る。
kindle-dash の `gasgauge-info -c` は "82%" を返すので、数字以外は捨てて受け取る。
最後に受け取った値を DATA_DIR/kindle.json に保存し、/preview などでも同じ値を出す。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from .claude import _parse_time, read_json, write_json

# これより古い値は当てにならないので出さない（Kindle が止まっている可能性がある）
STALE = timedelta(hours=6)


def data_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "kindle.json"


def parse_battery(raw: str | None) -> int | None:
    m = re.search(r"\d+", raw or "")
    if not m:
        return None
    return max(0, min(100, int(m.group())))


def save_battery(level: int) -> bool:
    """保存して、前回から値が変わったら True"""
    old = read_json(data_file()) or {}
    write_json(data_file(), {"battery": level, "at": datetime.now().astimezone().isoformat()})
    return old.get("battery") != level


def view(now: datetime | None = None) -> dict | None:
    data = read_json(data_file())
    if not data or data.get("battery") is None:
        return None
    now = now or datetime.now().astimezone()
    at = _parse_time(data.get("at"))
    if at and now - at > STALE:
        return None
    level = int(data["battery"])
    return {"level": level, "low": level <= 20}
