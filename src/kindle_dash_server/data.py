"""ダッシュボードに載せるデータを集める。

collect() は保存済みのデータを読むだけで、外部には取りに行かない（描画を待たせないため）。
天気は weather_loop() が裏で定期取得して DATA_DIR/weather.json に保存する。
表示項目を増やしたいときは、取得は裏のループに、表示は collect() の戻り値に足す。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import kindle, train, usage
from .claude import _parse_time, read_json, write_json

log = logging.getLogger(__name__)

# これより古い天気は出さない（予報がずれるため）
WEATHER_STALE = timedelta(hours=3)

# WMO天気コード → 日本語
WEATHER_TEXT = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",
    45: "霧", 48: "霧",
    51: "霧雨", 53: "霧雨", 55: "霧雨", 56: "着氷性の霧雨", 57: "着氷性の霧雨",
    61: "小雨", 63: "雨", 65: "大雨", 66: "着氷性の雨", 67: "着氷性の雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
    80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "にわか雪",
    95: "雷雨", 96: "雷雨（ひょう）", 99: "雷雨（ひょう）",
}

WEEKDAYS = "月火水木金土日"


def weather_text(code: int | None) -> str:
    if code is None:
        return "—"
    return WEATHER_TEXT.get(int(code), "不明")


async def fetch_weather() -> dict:
    params = {
        "latitude": (os.getenv("LATITUDE") or "34.6937"),
        "longitude": (os.getenv("LONGITUDE") or "135.5023"),
        "timezone": (os.getenv("TZ") or "Asia/Tokyo"),
        "current": "temperature_2m,relative_humidity_2m,weather_code",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 4,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        r.raise_for_status()
        return r.json()


def shape_weather(raw: dict, now: datetime) -> dict:
    cur = raw["current"]
    hourly = raw["hourly"]
    daily = raw["daily"]

    # これから先の3時間ごと×6コマ
    hours = []
    for i, t in enumerate(hourly["time"]):
        ts = datetime.fromisoformat(t)
        if ts < now.replace(minute=0, second=0, microsecond=0, tzinfo=None):
            continue
        if (ts.hour % 3) != 0:
            continue
        hours.append({
            "hour": ts.hour,
            "temp": round(hourly["temperature_2m"][i]),
            "rain": hourly["precipitation_probability"][i],
            "text": weather_text(hourly["weather_code"][i]),
        })
        if len(hours) == 6:
            break

    days = []
    for i, d in enumerate(daily["time"]):
        dt = datetime.fromisoformat(d)
        days.append({
            "label": "今日" if i == 0 else f"{dt.month}/{dt.day}（{WEEKDAYS[dt.weekday()]}）",
            "text": weather_text(daily["weather_code"][i]),
            "max": round(daily["temperature_2m_max"][i]),
            "min": round(daily["temperature_2m_min"][i]),
            "rain": daily["precipitation_probability_max"][i],
        })

    return {
        "temp": round(cur["temperature_2m"]),
        "humidity": cur["relative_humidity_2m"],
        "text": weather_text(cur["weather_code"]),
        "hours": hours,
        "today": days[0],
        "days": days[1:],
    }


def weather_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "weather.json"


def weather_minutes() -> float:
    return float(os.getenv("WEATHER_POLL_MINUTES") or "10")


async def refresh_weather() -> bool:
    """天気を取得して保存する。失敗したら前回の分を残して False"""
    try:
        raw = await fetch_weather()
    except Exception as e:
        log.warning("天気を取得できませんでした（%s）。前回の分を使います", type(e).__name__)
        return False
    write_json(weather_file(), {"raw": raw, "fetched_at": datetime.now().astimezone().isoformat()})
    return True


async def weather_loop(on_update=None) -> None:
    """起動直後に1回、その後 WEATHER_POLL_MINUTES ごとに取得する"""
    while True:
        try:
            if await refresh_weather() and on_update:
                on_update()
        except Exception:  # ループ自体は止めない
            log.exception("天気の取得に失敗")
        await asyncio.sleep(max(1.0, weather_minutes()) * 60)


async def refresh_all() -> None:
    """天気と時刻表を1回取りに行く（スナップショット用。サーバーでは各ループが行う）"""
    await asyncio.gather(refresh_weather(), train.refresh())


def load_weather(now: datetime) -> tuple[dict | None, str | None]:
    """保存済みの天気を表示用にする。戻り値は (天気, エラー文)"""
    saved = read_json(weather_file())
    if not saved:
        return None, "天気をまだ取得できていません"
    fetched = _parse_time(saved.get("fetched_at"))
    if fetched and now.astimezone() - fetched > WEATHER_STALE:
        return None, f"天気を {fetched.hour}:{fetched.minute:02d} から取得できていません"
    try:
        return shape_weather(saved["raw"], now), None
    except Exception as e:  # 保存内容が壊れていても日付だけは出す
        return None, f"天気を表示できませんでした（{type(e).__name__}）"


def collect() -> dict:
    now = datetime.now()
    weather, error = load_weather(now)
    show_train = (os.getenv("SHOW_TRAIN") or "1") != "0"
    return {
        "place": (os.getenv("PLACE_NAME") or "大阪"),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "weekday": WEEKDAYS[now.weekday()],
        "updated": now.strftime("%H:%M"),
        "weather": weather,
        "error": error,
        # AI の使用量（Claude・Codex・Antigravity）を1行1サービスの表にしたもの
        "usage": usage.rows(),
        "usage_headers": usage.HEADERS,
        "battery": kindle.view(),
        "show_train": show_train,
        "train": train.view(now) if show_train else None,
    }
