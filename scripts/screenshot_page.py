#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页截图（Playwright 渲染取证/兜底）。

用法:
  python screenshot_page.py <url> <output.png> [--full] [--selector CSS] [--wait MS] [--width N]
  python screenshot_page.py <url1> <url2> --outdir D:/shots        # 批量（文件名取域名+序号）
选项:
  --full         整页截图（默认仅视口）
  --selector CSS 只截指定元素（如 "article" / "#main"）
  --wait MS      额外等待毫秒（默认 1500，SPA/慢资源加大）
  --width N      视口宽度（默认 1280）
输出: 成功打印落盘路径；批量时输出 JSON 数组
依赖: playwright（用系统 Chrome channel=chrome，免下 chromium build）
"""
import argparse
import json
import os
import sys
from urllib.parse import urlparse


def _launch(p):
    try:
        return p.chromium.launch(headless=True, channel="chrome")  # 系统 Chrome（免下 chromium build）
    except Exception:
        return p.chromium.launch(headless=True)


def screenshot(url, output_path, full=False, selector=None, wait_ms=1500, width=1280):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": width, "height": 900},
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        try:
            try:
                page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(wait_ms)
            if selector:
                el = page.query_selector(selector)
                if not el:
                    raise RuntimeError(f"选择器未命中：{selector}")
                el.screenshot(path=output_path)
            else:
                page.screenshot(path=output_path, full_page=full)
        finally:
            browser.close()
    return output_path


def main():
    ap = argparse.ArgumentParser(description="网页截图（Playwright；--full 整页/--selector 元素）")
    ap.add_argument("pos", nargs="+",
                    help="单张: <url> <output.png>；批量: <url...> --outdir 目录")
    ap.add_argument("--outdir", help="批量模式：输出目录")
    ap.add_argument("--full", action="store_true", help="整页截图（默认仅视口）")
    ap.add_argument("--selector", help="只截指定 CSS 元素")
    ap.add_argument("--wait", type=int, default=1500, help="额外等待毫秒（默认 1500）")
    ap.add_argument("--width", type=int, default=1280, help="视口宽度（默认 1280）")
    args = ap.parse_args()

    if args.outdir:  # 批量：pos 全部是 URL
        urls, out_single = args.pos, None
    else:            # 单张：pos = [url, output]
        if len(args.pos) != 2:
            print("用法: screenshot_page.py <url> <output.png> [--full] [--selector CSS]；"
                  "批量: screenshot_page.py <url...> --outdir 目录", file=sys.stderr)
            sys.exit(1)
        urls, out_single = args.pos[:1], args.pos[1]

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        results = []
        for i, u in enumerate(urls, 1):
            host = urlparse(u).netloc.replace(":", "_") or f"page{i}"
            out = os.path.join(args.outdir, f"{i:02d}_{host}.png")
            try:
                screenshot(u, out, args.full, args.selector, args.wait, args.width)
                results.append({"url": u, "path": out, "size": os.path.getsize(out)})
            except Exception as e:
                results.append({"url": u, "error": f"{type(e).__name__} {str(e)[:100]}"})
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    try:
        screenshot(urls[0], out_single, args.full, args.selector, args.wait, args.width)
        print(out_single)
    except Exception as e:
        print(f"[截图失败] {type(e).__name__} {str(e)[:150]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
