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

from .data import collect

WIDTH = int(os.getenv("SCREEN_WIDTH", "1072"))
HEIGHT = int(os.getenv("SCREEN_HEIGHT", "1448"))
LANDSCAPE = os.getenv("ORIENTATION", "portrait") == "landscape"
ROTATE = int(os.getenv("LANDSCAPE_ROTATE", "90"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "60"))

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)

state: dict = {"browser": None, "png": None, "at": 0.0}
lock = asyncio.Lock()


async def launch_browser(pw) -> Browser:
    return await pw.chromium.launch(args=["--no-sandbox"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await async_playwright().start()
    state["browser"] = await launch_browser(pw)
    yield
    await state["browser"].close()
    await pw.stop()


app = FastAPI(lifespan=lifespan)


def viewport() -> tuple[int, int]:
    # 横置きなら横長で描画して、あとで縦長に回転させる
    return (HEIGHT, WIDTH) if LANDSCAPE else (WIDTH, HEIGHT)


async def render_html() -> str:
    w, h = viewport()
    ctx = await collect()
    return env.get_template("dashboard.html").render(**ctx, width=w, height=h, landscape=LANDSCAPE)


def to_kindle_png(screenshot: bytes) -> bytes:
    img = Image.open(io.BytesIO(screenshot)).convert("L")  # 8bitグレースケール
    if LANDSCAPE:
        img = img.rotate(ROTATE, expand=True)
    # E-Inkの16階調に丸めて、にじみを減らす
    img = img.point(lambda v: (v // 17) * 17)
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT))
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
async def dash_png():
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


@app.get("/health")
async def health():
    return {"ok": True}
