#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_page — 网页正文提取为 markdown（搜索→深读闭环的读半边）。

四层策略：
  1. bs4 启发式（国内站直连，首选）：去 script/nav/header/footer，取文本最长的正文容器
  2. Playwright 渲染（层1 正文过短时自动触发，对付 JS 渲染页）
  3. StealthyFetcher 反检测（层2 也失败时触发——Cloudflare 类反爬站，慢 10-30s）
  4. r.jina.ai（--jina 显式指定；国际站适用——对国内政府/学校站常超时，勿默认）

用法:
  python read_page.py <url> [--jina] [--max-chars 6000]
  python read_page.py <url1> <url2> ...            # 批量，输出 JSON 数组
输出: 单 URL 时 markdown 到 stdout；多 URL 时 JSON [{url,title,content,method}]
依赖: requests+bs4（已装）；playwright（可选兜底，已装 chromium）；
      scrapling[fetchers]+patchright chromium（第三层，未装则该层跳过）
"""
import argparse
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

from common import headers, load_config, proxies_for

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "iframe",
              "form", "noscript", "svg", "button", "select", "input")


def _fetch(url, timeout=15, config=None):
    return requests.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                        timeout=timeout, proxies=proxies_for("_read_direct", config) or None)


def _soup_to_markdown(soup):
    for t in soup(STRIP_TAGS):
        t.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")[:120]
    # 正文容器打分：取文本最长的候选
    candidates = soup.select("article, [role=main], main, #content, .content, .article, .text")
    best = max(candidates + [soup.body or soup], key=lambda c: len(c.get_text(strip=True)))
    lines = [f"# {title}"] if title else []
    for el in best.descendants:
        if el.name in ("h1", "h2", "h3"):
            txt = el.get_text(" ", strip=True)
            if txt:
                lines.append("\n" + "#" * int(el.name[1]) + " " + txt)
        elif el.name == "p":
            txt = el.get_text(" ", strip=True)
            if len(txt) >= 4:
                lines.append(txt)
        elif el.name == "li":
            txt = el.get_text(" ", strip=True)
            if txt:
                lines.append("- " + txt)
        elif el.name == "tr" and el.find("td"):
            cells = [td.get_text(" ", strip=True) for td in el.find_all(["td", "th"])]
            lines.append("| " + " | ".join(cells) + " |")
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text.strip()


def read_via_bs4(url, timeout=15, config=None):
    r = _fetch(url, timeout, config)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.apparent_encoding)
    return _soup_to_markdown(soup)


def read_via_playwright(url, timeout=30):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, channel="chrome")  # 系统 Chrome（免下 chromium build）
        except Exception:
            b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1280, "height": 900}, user_agent=UA)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            pg.wait_for_timeout(2500)
            html = pg.content()
        finally:
            b.close()
    return _soup_to_markdown(BeautifulSoup(html, "html.parser"))


def read_via_stealthy(url, timeout=60):
    """第三层：StealthyFetcher（patchright 反检测）——Cloudflare 类反爬站兜底。
    patchright chromium 未装时自动指系统 Chrome（real_chrome=True 指纹更优）。
    scrapling 未装/无可用浏览器抛异常，由 read() 跳过。慢（15-30s），勿放前层。"""
    from common import find_chrome
    from scrapling.fetchers import StealthyFetcher
    chrome = find_chrome()
    kwargs = {"headless": True, "timeout": timeout * 1000, "block_ads": True,
              "blocked_domains": ["hm.baidu.com", "pos.baidu.com", "cpro.baidu.com"]}
    if chrome:  # 真 Chrome 指纹 + 免 patchright 下载
        kwargs.update({"real_chrome": True, "executable_path": chrome})
    r = StealthyFetcher.fetch(url, solve_cloudflare=True, **kwargs)
    if r.status != 200:
        raise RuntimeError(f"stealthy status {r.status}")
    html = r.body.decode("utf-8", "ignore") if isinstance(r.body, bytes) else str(r.body)
    return _soup_to_markdown(BeautifulSoup(html, "html.parser"))


def read_via_jina(url, timeout=40, config=None):
    # 2025 起 r.jina.ai 匿名通道返回 403，需 JINA_API_KEY（env 或 secrets.json["jina"]）
    from common import api_key
    proxy = proxies_for("jina", config)
    hdr = {"User-Agent": UA}
    key = api_key("jina", config)
    if key:
        hdr["Authorization"] = f"Bearer {key}"
    r = requests.get(f"https://r.jina.ai/{url}", timeout=timeout,
                     headers=hdr, proxies=proxy)
    r.raise_for_status()
    text = r.text
    m = re.match(r"Title:\s*(.+)", text)
    title = m.group(1).strip() if m else ""
    body = text.split("Markdown Content:", 1)[-1].strip() if "Markdown Content:" in text else text
    return title, body


def read(url, config=None, allow_jina=False, min_chars=200):
    """单 URL 主流程：bs4 → (过短) playwright → (仍失败) stealthy → (显式) jina。
    返回 (title, content, method)。"""
    try:
        title, content = read_via_bs4(url, config=config)
        if len(content) >= min_chars:
            return title, content, "bs4"
    except Exception:
        title, content = "", ""
    try:
        title, content = read_via_playwright(url)
        if len(content) >= min_chars:
            return title, content, "playwright"
    except Exception:
        pass
    try:
        title, content = read_via_stealthy(url)
        if len(content) >= min_chars:
            return title, content, "stealthy"
    except Exception:
        pass
    if allow_jina:
        try:
            title, content = read_via_jina(url, config=config)
            return title, content, "jina"
        except Exception:
            pass
    return (title or "", content or "", "failed")


def main():
    ap = argparse.ArgumentParser(description="网页正文提取（bs4→playwright→stealthy→jina 四层）")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--jina", action="store_true", help="国际站走 r.jina.ai（国内站勿用，常超时）")
    ap.add_argument("--max-chars", type=int, default=6000, help="单页正文截断长度")
    args = ap.parse_args()
    config = load_config()

    results = []
    for u in args.urls:
        title, content, method = read(u, config=config, allow_jina=args.jina)
        results.append({"url": u, "title": title, "method": method,
                        "content": content[:args.max_chars]})
    if len(results) == 1:
        r = results[0]
        if r["method"] == "failed":
            print(f"[提取失败] {r['url']}", file=sys.stderr)
            sys.exit(1)
        print(f"# {r['title']}\n\n{r['content']}")
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
