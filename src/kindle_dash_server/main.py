from __future__ import annotations

import asyncio
import io
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from playwright.async_api import Browser, async_playwright

from . import claude
from .data import collect

WIDTH = int(os.getenv("SCREEN_WIDTH", "1072"))
HEIGHT = int(os.getenv("SCREEN_HEIGHT", "1448"))
LANDSCAPE = os.getenv("ORIENTATION", "portrait") == "landscape"
# 横置き時の回転角。0 なら横長画像のまま出力、90/270 なら縦長に回して出力
ROTATE = int(os.getenv("LANDSCAPE_ROTATE", "0"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "60"))
# 設定すると POST /claude-usage に Authorization: Bearer <値> が必要になる
PUSH_TOKEN = os.getenv("PUSH_TOKEN") or None

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)

state: dict = {"browser": None, "png": None, "at": 0.0}
lock = asyncio.Lock()


async def launch_browser(pw) -> Browser:
    return await pw.chromium.launch(args=["--no-sandbox"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await async_playwright().start()
    state["browser"] = await launch_browser(pw)
    poller = None
    if claude.poll_enabled():
        # 取れたら画像キャッシュを捨てて、次のアクセスで描き直す
        poller = asyncio.create_task(claude.poll_loop(on_update=lambda: state.update(png=None)))
    yield
    if poller:
        poller.cancel()
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


@app.post("/claude-usage")
async def push_claude_usage(request: Request, authorization: str | None = Header(None)):
    """PC から Claude の使用量を受け取る（kindle-dash-push-claude が送ってくる）"""
    if PUSH_TOKEN and authorization != f"Bearer {PUSH_TOKEN}":
        raise HTTPException(401, "invalid token")
    try:
        data = claude.save(await request.json())
    except ValueError as e:  # JSON不正・必要な項目なし
        raise HTTPException(422, str(e)) from e
    state["png"] = None  # 次のアクセスで描き直す
    return data


@app.get("/claude-usage")
async def get_claude_usage():
    return claude.load() or {}


@app.get("/health")
async def health():
    return {"ok": True}
