"""kindle-dash-server のコマンド入口。

  uv run kindle-dash-server            … サーバー起動（--reload でコード変更を自動反映）
  uv run kindle-dash-snapshot -o x.png … サーバーを立てずにPNGを1枚書き出す
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path


def log_config() -> dict:
    """uvicorn とアプリのログを、時刻つきの同じ形式で標準出力に出す設定。

    uvicorn に渡すと、--reload で起動し直した子プロセスにも同じ設定が効く。
    """
    datefmt = "%Y-%m-%d %H:%M:%S"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "app": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s", "datefmt": datefmt},
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s INFO access: %(client_addr)s "%(request_line)s" %(status_code)s',
                "datefmt": datefmt,
                "use_colors": False,
            },
        },
        "handlers": {
            "app": {"class": "logging.StreamHandler", "formatter": "app", "stream": "ext://sys.stdout"},
            "access": {"class": "logging.StreamHandler", "formatter": "access", "stream": "ext://sys.stdout"},
        },
        "root": {"handlers": ["app"], "level": "INFO"},
        "loggers": {
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            # 取得のたびに URL を1行出すので、警告以上だけにする
            "httpx": {"level": "WARNING"},
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Kindle用ダッシュボード画像サーバー")
    p.add_argument("--host", default=(os.getenv("HOST") or "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.getenv("PORT") or "8080"))
    p.add_argument("--reload", action="store_true", help="コード・テンプレート変更で自動再起動（開発用）")
    args = p.parse_args()

    import uvicorn

    uvicorn.run(
        "kindle_dash_server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=log_config(),
        # テンプレートの変更でも再起動させる
        reload_includes=["*.py", "*.html"] if args.reload else None,
    )


def snapshot() -> None:
    p = argparse.ArgumentParser(description="ダッシュボードPNGを1枚書き出す")
    p.add_argument("-o", "--output", default="out/dash.png", help="出力先（既定: out/dash.png）")
    p.add_argument("--html", help="描画前のHTMLも保存する場合のパス")
    args = p.parse_args()

    from playwright.async_api import async_playwright

    from . import data
    from .main import launch_browser, render_html, render_png

    async def run() -> None:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        # サーバーでは裏で定期取得している天気と時刻表を、ここでは描く前に1回取りに行く
        await data.refresh_all()
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
