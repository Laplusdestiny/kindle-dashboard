"""kindle-dash-server のコマンド入口。

  uv run kindle-dash-server            … サーバー起動（--reload でコード変更を自動反映）
  uv run kindle-dash-snapshot -o x.png … サーバーを立てずにPNGを1枚書き出す
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description="Kindle用ダッシュボード画像サーバー")
    p.add_argument("--host", default=(os.getenv("HOST") or "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.getenv("PORT") or "8080"))
    p.add_argument("--reload", action="store_true", help="コード・テンプレート変更で自動再起動（開発用）")
    args = p.parse_args()

    import logging

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

    uvicorn.run(
        "kindle_dash_server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        # テンプレートの変更でも再起動させる
        reload_includes=["*.py", "*.html"] if args.reload else None,
    )


def snapshot() -> None:
    p = argparse.ArgumentParser(description="ダッシュボードPNGを1枚書き出す")
    p.add_argument("-o", "--output", default="out/dash.png", help="出力先（既定: out/dash.png）")
    p.add_argument("--html", help="描画前のHTMLも保存する場合のパス")
    args = p.parse_args()

    from playwright.async_api import async_playwright

    from .main import launch_browser, render_html, render_png

    async def run() -> None:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.html:
            Path(args.html).write_text(await render_html(), encoding="utf-8")
        async with async_playwright() as pw:
            browser = await launch_browser(pw)
            try:
                out.write_bytes(await render_png(browser))
            finally:
                await browser.close()
        print(f"wrote {out}")

    asyncio.run(run())
