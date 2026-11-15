from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from playwright.async_api import Browser, async_playwright

from . import agy, claude, codex, data, kindle, train
from .data import collect

log = logging.getLogger(__name__)

WIDTH = int(os.getenv("SCREEN_WIDTH") or "1072")
HEIGHT = int(os.getenv("SCREEN_HEIGHT") or "1448")
LANDSCAPE = (os.getenv("ORIENTATION") or "landscape") == "landscape"
# 横置き時の回転角（反時計回り）。0 なら横長画像のまま、90/270 なら縦長に回して出力。
# Kindle の eips は画像を回転せずに縦長の画面へ描くので、実機では 90 か 270 にする
ROTATE = int(os.getenv("LANDSCAPE_ROTATE") or "90")
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS") or "60")
# 1枚の描画にかけてよい秒数。超えたら打ち切って前回の画像を返す
RENDER_TIMEOUT = float(os.getenv("RENDER_TIMEOUT_SECONDS") or "20")
# 使用量の取得（Claude → Codex → Antigravity）を始める間隔（秒）。3つの CLI を同時に動かさないため
POLL_STAGGER = 300

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)

# png: キャッシュ中の画像（無効にするときは None）、last_good: 最後に描けた画像（描画に失敗したときに返す）
state: dict = {"pw": None, "browser": None, "png": None, "at": 0.0, "last_good": None}
lock = asyncio.Lock()


def last_good_file() -> Path:
    return Path(os.getenv("DATA_DIR") or "data") / "last_dash.png"


async def launch_browser(pw) -> Browser:
    return await pw.chromium.launch(args=["--no-sandbox"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["pw"] = await async_playwright().start()
    state["browser"] = await launch_browser(state["pw"])
    try:  # 再起動の直後に描画に失敗しても、前回の画像を返せるようにする
        state["last_good"] = last_good_file().read_bytes()
    except OSError:
        pass
    # 取れたら画像キャッシュを捨てて、次のアクセスで描き直す
    invalidate = lambda: state.update(png=None)  # noqa: E731
    pollers = [
        asyncio.create_task(data.weather_loop(on_update=invalidate)),
        asyncio.create_task(train.poll_loop(on_update=invalidate)),
    ]
    # 使用量の取得は重い CLI を動かすので、開始を POLL_STAGGER 秒ずつずらす
    delay = 30.0
    for mod in (claude, codex, agy):
        if mod.poll_enabled():
            pollers.append(asyncio.create_task(mod.poll_loop(on_update=invalidate, start_delay=delay)))
            delay += POLL_STAGGER
    yield
    for t in pollers:
        t.cancel()
    await state["browser"].close()
    await state["pw"].stop()


app = FastAPI(lifespan=lifespan)


def viewport() -> tuple[int, int]:
    # SCREEN_WIDTH/HEIGHT はどちらの順で書いても良い。向きは ORIENTATION だけで決める
    short, long = sorted((WIDTH, HEIGHT))
    return (long, short) if LANDSCAPE else (short, long)


async def render_html() -> str:
    w, h = viewport()
    ctx = collect()  # 保存済みのデータを読むだけ（外部には取りに行かない）
    return env.get_template("dashboard.html").render(**ctx, width=w, height=h, landscape=LANDSCAPE)


def output_size() -> tuple[int, int]:
    """配信するPNGのサイズ。横置き＋回転なしのときだけ横長になる"""
    w, h = viewport()
    return (h, w) if LANDSCAPE and ROTATE in (90, 270) else (w, h)


def to_kindle_png(screenshot: bytes) -> bytes:
    img = Image.open(io.BytesIO(screenshot)).convert("L")  # 8bitグレースケール
    if LANDSCAPE and ROTATE:
        img = img.rotate(ROTATE, expand=True)
    # E-Inkの16階調に丸めて、にじみを減らす
    img = img.point(lambda v: (v // 17) * 17)
    if img.size != output_size():
        img = img.resize(output_size())
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def render_png(browser: Browser) -> bytes:
    w, h = viewport()
    page = await browser.new_page(viewport={"width": w, "height": h})
    try:
        await page.set_content(await render_html(), wait_until="load")
        await page.evaluate("document.fonts.ready")
        shot = await page.screenshot(type="png", full_page=False)
    finally:
        await page.close()
    return to_kindle_png(shot)


async def restart_browser() -> None:
    """描画に失敗したあと、Chromium を起動し直す（固まったままのブラウザを使い続けないため）"""
    try:
        await asyncio.wait_for(state["browser"].close(), 10)
    except Exception:
        pass
    try:
        state["browser"] = await launch_browser(state["pw"])
        log.info("Chromium を起動し直しました")
    except Exception:
        log.exception("Chromium を起動し直せませんでした")


@app.get("/dash.png")
async def dash_png(battery: str | None = None):
    """Kindle が取りに来る画像。?battery=82 で電池残量も受け取る。

    描画に失敗したり RENDER_TIMEOUT 秒を超えたりしたら、前回描けた画像を返す
    （Kindle を待たせたままにしない。画面は古いまま残るだけで済む）。
    """
    started = time.perf_counter()
    level = kindle.parse_battery(battery)
    if level is not None and kindle.save_battery(level):
        state["png"] = None  # 残量が変わったら描き直す
    async with lock:
        waited = time.perf_counter() - started
        if state["png"] is not None and time.time() - state["at"] <= CACHE_SECONDS:
            png, how = state["png"], "キャッシュ"
        else:
            try:
                png = await asyncio.wait_for(render_png(state["browser"]), RENDER_TIMEOUT)
            except Exception as e:  # タイムアウトも含む
                png = state["last_good"]
                how = f"描画に失敗（{type(e).__name__}）→ 前回の画像"
                log.warning("描画に失敗しました: %s: %s", type(e).__name__, str(e).splitlines()[0] if str(e) else "")
                await restart_browser()
            else:
                how = "描画"
                state.update(png=png, at=time.time(), last_good=png)
                try:
                    last_good_file().parent.mkdir(parents=True, exist_ok=True)
                    last_good_file().write_bytes(png)
                except OSError:
                    pass
    total = time.perf_counter() - started
    log.info("dash.png: %s %.2f秒（順番待ち %.2f秒、battery=%s）", how, total, waited, level)
    if png is None:
        return Response("まだ1枚も描けていません", status_code=503, media_type="text/plain; charset=utf-8")
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/preview", response_class=HTMLResponse)
async def preview():
    """ブラウザでレイアウトを確認する用（PCで開く）"""
    return await render_html()


@app.get("/claude-usage")
async def get_claude_usage():
    return claude.load() or {}


@app.get("/codex-usage")
async def get_codex_usage():
    return codex.load() or {}


@app.get("/agy-usage")
async def get_agy_usage():
    return agy.load() or {}


@app.get("/health")
async def health():
    return {"ok": True}
