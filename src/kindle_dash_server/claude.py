"""Claude のプラン使用量（5時間枠・週間枠）を受け取って保存し、表示用に整える。

NAS 側は Claude のトークンを持たない。PC 側（Claude Code が動くマシン）から
`kindle-dash-push-claude` で POST /claude-usage に送ってもらう方式。
受け付ける JSON は次のどれでもよい:
  - Claude Code の statusline に渡される JSON 全体（rate_limits を含む）
  - その rate_limits 部分だけ
  - /api/oauth/usage の応答（five_hour.utilization など）
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

WINDOWS = {"five_hour": "5時間", "seven_day": "週間"}
WEEKDAYS = "月火水木金土日"


def data_file() -> Path:
    return Path(os.getenv("DATA_DIR", "data")) / "claude_usage.json"


def _parse_time(v) -> datetime | None:
    """epoch秒・epochミリ秒・ISO文字列のどれでも受ける。返り値はローカル時刻（aware）"""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e12 else v).astimezone()
    s = str(v).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return (dt if dt.tzinfo else dt.astimezone()).astimezone()


def normalize(payload: dict) -> dict:
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
    return {"windows": windows, "received_at": datetime.now().astimezone().isoformat()}


def save(payload: dict) -> dict:
    data = normalize(payload)
    path = data_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 書きかけを読まれないように置き換えで保存
    return data


def load() -> dict | None:
    try:
        return json.loads(data_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _when(dt: datetime, now: datetime) -> str:
    hm = f"{dt.hour}:{dt.minute:02d}"
    if dt.date() == now.date():
        return hm
    return f"{dt.month}/{dt.day}（{WEEKDAYS[dt.weekday()]}）{hm}"


def view(now: datetime | None = None) -> dict | None:
    """テンプレート用。データが無ければ None"""
    data = load()
    if not data:
        return None
    now = now or datetime.now().astimezone()
    rows = []
    for key, label in WINDOWS.items():
        w = data["windows"].get(key)
        if not w:
            continue
        reset = _parse_time(w.get("resets_at"))
        used = max(0.0, min(100.0, w["used"]))
        if reset and reset <= now:
            # 受信後にリセット時刻を過ぎていれば、枠は空いているとみなす
            rows.append({"label": label, "used": 0, "left": 100, "reset": "リセット済み"})
            continue
        rows.append({
            "label": label,
            "used": round(used),
            "left": round(100 - used),
            "reset": f"{_when(reset, now)} リセット" if reset else "",
        })
    received = _parse_time(data.get("received_at"))
    return {"rows": rows, "received": _when(received, now) if received else "—"}
