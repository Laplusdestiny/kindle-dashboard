from __future__ import annotations

import asyncio
import io
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from playwright.async_api import Browser, async_playwright

from . import agy, claude, codex, kindle
from .data import collect

WIDTH = int(os.getenv("SCREEN_WIDTH") or "1072")
HEIGHT = int(os.getenv("SCREEN_HEIGHT") or "1448")
LANDSCAPE = (os.getenv("ORIENTATION") or "landscape") == "landscape"
# 横置き時の回転角（反時計回り）。0 なら横長画像のまま、90/270 なら縦長に回して出力。
# Kindle の eips は画像を回転せずに縦長の画面へ描くので、実機では 90 か 270 にする
ROTATE = int(os.getenv("LANDSCAPE_ROTATE") or "90")
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS") or "60")

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)

state: dict = {"browser": None, "png": None, "at": 0.0}
lock = asyncio.Lock()


async def launch_browser(pw) -> Browser:
    return await pw.chromium.launch(args=["--no-sandbox"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await async_playwright().start()
    state["browser"] = await launch_browser(pw)
    pollers = []
    # 取れたら画像キャッシュを捨てて、次のアクセスで描き直す
    invalidate = lambda: state.update(png=None)  # noqa: E731
    if claude.poll_enabled():
        pollers.append(asyncio.create_task(claude.poll_loop(on_update=invalidate)))
    if codex.poll_enabled():
        pollers.append(asyncio.create_task(codex.poll_loop(on_update=invalidate)))
    if agy.poll_enabled():
        pollers.append(asyncio.create_task(agy.poll_loop(on_update=invalidate)))
    yield
    for t in pollers:
        t.cancel()
    await state["browser"].close()
    await pw.stop()


app = FastAPI(lifespan=lifespan)


def viewport() -> tuple[int, int]:
    # SCREEN_WIDTH/HEIGHT はどちらの順で書いても良い。向きは ORIENTATION だけで決める
    short, long = sorted((WIDTH, HEIGHT))
    return (long, short) if LANDSCAPE else (short, long)


async def render_html() -> str:
    w, h = viewport()
    ctx = await collect()
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


@app.get("/dash.png")
async def dash_png(battery: str | None = None):
    """Kindle が取りに来る画像。?battery=82 で電池残量も受け取る"""
    level = kindle.parse_battery(battery)
    if level is not None and kindle.save_battery(level):
        state["png"] = None  # 残量が変わったら描き直す
    async with lock:
        if state["png"] is None or time.time() - state["at"] > CACHE_SECONDS:
            state["png"] = await render_png(state["browser"])
            state["at"] = time.time()
        png = state["png"]
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
