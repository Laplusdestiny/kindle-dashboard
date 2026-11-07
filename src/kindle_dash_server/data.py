"""ダッシュボードに載せるデータを集める。

表示項目を増やしたいときは、ここに取得関数を足して
collect() の戻り値に入れ、テンプレートで使う。
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx

from . import claude, train

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


async def collect() -> dict:
    now = datetime.now()
    ctx = {
        "place": (os.getenv("PLACE_NAME") or "大阪"),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "weekday": WEEKDAYS[now.weekday()],
        "updated": now.strftime("%H:%M"),
        "weather": None,
        "error": None,
        "show_claude": (os.getenv("SHOW_CLAUDE") or "1") != "0",
        "claude": claude.view(),
        "show_train": (os.getenv("SHOW_TRAIN") or "1") != "0",
        "train": None,
    }
    if ctx["show_train"]:
        ctx["train"] = await train.view(now)
    try:
        ctx["weather"] = shape_weather(await fetch_weather(), now)
    except Exception as e:  # 天気が取れなくても日付だけは出す
        ctx["error"] = f"天気を取得できませんでした（{type(e).__name__}）"
    return ctx
