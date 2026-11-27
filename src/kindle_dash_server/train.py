"""最寄り駅の発車時刻（初期値は阪急宝塚線 岡町駅の大阪梅田方面）。

阪急公式サイトの駅時刻表ページ（平日／土曜・休日）を取得して解析し、
DATA_DIR/train/ に保存する。取得は裏のループ（poll_loop）が行い、時刻表は1日1回だけ取り直す。
描画のとき（view）は保存済みのファイルだけを読んで、
「今から TRAIN_WINDOW_MINUTES 分以内」の発車を計算する（外部には取りに行かない）。
駅まで TRAIN_WALK_MINUTES 分かかるので、それより早く出る電車は出さない。表は TRAIN_MAX_ROWS 本まで。

平日か土休日かは、内閣府の祝日CSVで判定する（取れなければ土日だけで判定）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

DEFAULT_URL = "https://www.hankyu.co.jp/station/html/HK-45_ta_1_{day}.html"
HOLIDAY_URL = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"
HEADERS = {"User-Agent": "kindle-dash-server/0.1 (personal dashboard)"}
TIMETABLE_MAX_AGE = 24 * 3600      # 時刻表は1日1回取り直す（ダイヤ改正に追従するため）
HOLIDAY_MAX_AGE = 30 * 24 * 3600   # 祝日CSVは月1回で十分
DAY_START_HOUR = 3                 # これより前の時刻は前日のダイヤとして扱う

# 時刻表ページの「時」「分」「種別」「行き先」を出てくる順に拾う
TOKEN = re.compile(
    r'class="timetable_hour[^"]*"><span[^>]*>(?P<hour>\d+)</span>'
    r'|class="timetable_min"><span[^>]*>(?P<min>\d+)</span>'
    r'|class="timetable_train_type">(?P<type>[^<]*)<'
    r'|class="timetable_station_name">(?P<dest>[^<]*)<'
)


def _dir() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "train"


def window_minutes() -> int:
    return int(os.getenv("TRAIN_WINDOW_MINUTES") or "30")


def walk_minutes() -> int:
    """駅まで歩く分数。これより早く出る電車は間に合わないので出さない"""
    return max(0, int(os.getenv("TRAIN_WALK_MINUTES") or "0"))


def max_rows() -> int:
    """表に出す本数の上限（0 なら制限しない）。9本以上は天気の表示と重なる"""
    return max(0, int(os.getenv("TRAIN_MAX_ROWS") or "8"))


def parse_timetable(html: str) -> list[dict]:
    """時刻表HTML → [{"t": 始発日0時からの分, "type": "普通", "dest": "大阪梅田"}, ...]"""
    trains: list[dict] = []
    hour = None
    for m in TOKEN.finditer(html):
        if m["hour"] is not None:
            hour = int(m["hour"])
        elif m["min"] is not None and hour is not None:
            h = hour + 24 if hour < DAY_START_HOUR else hour
            trains.append({"t": h * 60 + int(m["min"]), "type": "", "dest": ""})
        elif m["type"] is not None and trains:
            trains[-1]["type"] = m["type"].strip("［］[] ")
        elif m["dest"] is not None and trains:
            trains[-1]["dest"] = m["dest"].strip()
    if not trains:
        raise ValueError("時刻表から発車時刻を読み取れませんでした")
    return sorted(trains, key=lambda x: x["t"])


def _fresh(path: Path, max_age: int) -> bool:
    try:
        return time.time() - path.stat().st_mtime < max_age
    except FileNotFoundError:
        return False


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _timetable_path(day: str) -> tuple[str, Path]:
    """day は "w"（平日）か "h"（土曜・休日）"""
    url = (os.getenv("TRAIN_TIMETABLE_URL") or DEFAULT_URL).format(day=day)
    return url, _dir() / (re.sub(r"[^\w-]", "_", Path(url).stem) + ".json")


def _holiday_path() -> Path:
    return _dir() / "holidays.csv"


async def refresh() -> None:
    """古くなった時刻表と祝日CSVを取り直す。失敗したら前回の分を残す"""
    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        for day in ("w", "h"):
            url, path = _timetable_path(day)
            if _fresh(path, TIMETABLE_MAX_AGE):
                continue
            try:
                r = await client.get(url)
                r.raise_for_status()
                trains = parse_timetable(r.text)
                _write(path, json.dumps({"url": url, "trains": trains}, ensure_ascii=False))
                log.info("時刻表を更新しました: %s（%d本）", url, len(trains))
            except Exception as e:
                log.warning("時刻表を取得できませんでした。前回の分を使います: %s (%s)", url, e)
        if not _fresh(_holiday_path(), HOLIDAY_MAX_AGE):
            try:
                r = await client.get(HOLIDAY_URL)
                r.raise_for_status()
                _write(_holiday_path(), r.content.decode("cp932"))
            except Exception as e:
                log.warning("祝日CSVを取得できませんでした (%s)", e)


async def poll_loop(on_update=None) -> None:
    """起動直後に1回、その後1時間ごとに、古くなっていれば取り直す"""
    while True:
        try:
            await refresh()
            if on_update:
                on_update()
        except Exception:  # ループ自体は止めない
            log.exception("時刻表の取得に失敗")
        await asyncio.sleep(3600)


def _timetable(day: str) -> list[dict]:
    return json.loads(_timetable_path(day)[1].read_text(encoding="utf-8"))["trains"]


def _holidays() -> set[date]:
    days: set[date] = set()
    try:
        for row in csv.reader(io.StringIO(_holiday_path().read_text(encoding="utf-8"))):
            try:
                days.add(datetime.strptime(row[0], "%Y/%m/%d").date())
            except (ValueError, IndexError):
                continue  # 見出し行など
    except FileNotFoundError:
        pass
    return days


def _day_type(d: date, holidays: set[date]) -> str:
    return "h" if d.weekday() >= 5 or d in holidays else "w"


def view(now: datetime | None = None) -> dict:
    """テンプレート用。保存済みの時刻表が無ければ error を入れて返す"""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    span = window_minutes()
    walk = walk_minutes()
    limit = max_rows()
    out = {
        "label": (os.getenv("TRAIN_LABEL") or "岡町 → 大阪梅田"),
        "span": span,
        "walk": walk,
        "rows": [],
        "next": None,
        "error": None,
    }
    try:
        holidays = _holidays()
        tables: dict[str, list[dict]] = {}
        departures = []
        # 深夜は前日のダイヤ、終電後は翌日の始発も候補にする
        for offset in (-1, 0, 1):
            d = now.date() + timedelta(days=offset)
            kind = _day_type(d, holidays)
            if kind not in tables:
                tables[kind] = _timetable(kind)
            base = datetime.combine(d, datetime.min.time())
            for tr in tables[kind]:
                departures.append((base + timedelta(minutes=tr["t"]), tr))
    except FileNotFoundError:
        out["error"] = "時刻表をまだ取得できていません"
        return out
    except Exception as e:
        out["error"] = f"時刻表を読めませんでした（{type(e).__name__}）"
        return out

    departures.sort(key=lambda x: x[0])
    # 今から歩いて間に合う電車だけを候補にする（駅に着くのと同時に出る電車は間に合う扱い）
    upcoming = [(t, tr) for t, tr in departures if t >= now + timedelta(minutes=walk)]
    default_dest = out["label"].split("→")[-1].strip()
    for t, tr in upcoming:
        if t > now + timedelta(minutes=span) or (limit and len(out["rows"]) >= limit):
            break
        out["rows"].append({
            "time": f"{t.hour}:{t.minute:02d}",
            "kind": tr["type"],
            # 行き先がいつもと違うときだけ出す
            "dest": tr["dest"] if tr["dest"] and tr["dest"] != default_dest else "",
            "wait": int((t - now).total_seconds() // 60),
        })
    if not out["rows"] and upcoming:
        t = upcoming[0][0]
        out["next"] = f"{t.hour}:{t.minute:02d}"
    return out
