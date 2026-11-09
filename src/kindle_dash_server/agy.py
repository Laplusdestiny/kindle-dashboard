"""Antigravity CLI（agy）の使用量（クォータ）を受け取って保存する。

agy には使用量を返す公式のコマンドや API が無い（`/usage` は対話画面だけ、`-p` の出力や
フックにもクォータは入らない）。公式に読めるのは、statusline のコマンドに stdin で渡される
JSON の `quota` だけなので、PC 側の statusline から `kindle-dash-push-agy` で
POST /agy-usage に送ってもらう。

quota はバケットID → 状態の辞書で、実際には次のようなIDが来る（Google AI Pro で確認）:
  gemini-5h / gemini-weekly                 … Gemini のモデル
  3p-5h / 3p-weekly                         … Claude・GPT のモデル（third party。表示は Claude/GPT）
  gemini-argon-wtu-weekly                   … Gemini 4 Argon（表示は Argon）
各バケットは remaining_fraction（0〜1）、reset_time（ISO）、reset_in_seconds を持つ。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from .claude import _parse_time, read_json, write_json

# バケットIDの末尾 → 枠の長さ（分）
SUFFIX_MINUTES = {"5h": 300, "daily": 24 * 60, "weekly": 7 * 24 * 60}
# バケットIDの前半（モデルの系統）→ 表示名
FAMILY_NAMES = {"gemini": "Gemini", "3p": "Claude/GPT", "gemini-argon-wtu": "Argon"}


def data_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "agy_usage.json"


def split_bucket(bucket_id: str) -> tuple[str, int | None]:
    """'gemini-weekly' → ('gemini', 10080)。末尾が分からなければ (ID全体, None)"""
    family, _, suffix = bucket_id.rpartition("-")
    if family and suffix in SUFFIX_MINUTES:
        return family, SUFFIX_MINUTES[suffix]
    return bucket_id, None


def family_name(family: str) -> str:
    if family in FAMILY_NAMES:
        return FAMILY_NAMES[family]
    # 知らない系統は、短い記号を除いて頭を大文字にする（例: gemini-foo-x → Gemini Foo）
    words = [w.capitalize() for w in family.split("-") if len(w) > 3]
    return " ".join(words) or family


def normalize(payload: dict, source: str = "push") -> dict:
    """statusline JSON 全体か、その quota 部分を受け取る。メールアドレスなどは保存しない"""
    if not isinstance(payload, dict):
        raise ValueError("JSON オブジェクトではありません")
    quota = payload.get("quota", payload)
    if not isinstance(quota, dict):
        raise ValueError("quota がオブジェクトではありません")
    buckets = {}
    for bucket_id, q in quota.items():
        if not isinstance(q, dict) or q.get("disabled") or q.get("remaining_fraction") is None:
            continue  # 回数で数える枠（remaining_amount）や無効な枠は、割合で出せないので飛ばす
        family, minutes = split_bucket(bucket_id)
        reset = _parse_time(q.get("reset_time"))
        buckets[bucket_id] = {
            "family": family,
            "minutes": minutes,
            "used": (1 - float(q["remaining_fraction"])) * 100,
            "resets_at": reset.isoformat() if reset else None,
        }
    if not buckets:
        raise ValueError("quota に remaining_fraction のある枠が見つかりません")
    return {
        "buckets": buckets,
        "plan": payload.get("plan_tier"),
        "received_at": datetime.now().astimezone().isoformat(),
        "source": source,
    }


def save(payload: dict, source: str = "push") -> dict:
    data = normalize(payload, source)
    write_json(data_file(), data)
    return data


def load() -> dict | None:
    return read_json(data_file())


def families(data: dict | None) -> list[tuple[str, dict]]:
    """表示用に、系統ごとの枠をまとめる: [(表示名, {bucket_id: 枠}), ...]。Gemini → Claude・GPT → その他の順"""
    if not data:
        return []
    groups: dict[str, dict] = {}
    for bucket_id, b in data["buckets"].items():
        groups.setdefault(b["family"], {})[bucket_id] = b
    order = list(FAMILY_NAMES) + sorted(f for f in groups if f not in FAMILY_NAMES)
    return [(family_name(f), groups[f]) for f in order if f in groups]
