#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文搜图：多引擎图片搜索（Bing 图片 HTML + 百度图片 acjson，免 key，自动合并去重）。

用法:
  python image_search_text.py "函数图像 题目" [--count 10] [--engine bing,baidu]
  python image_search_text.py "洪崖洞夜景" --count 5 --out C:/temp/images   # 下载选中图
输出: JSON 数组 [{"title","url","source_page","width","height","engine"}]
依赖: requests, beautifulsoup4（--out 下载时标准库即可）
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from common import load_config, pick

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


def bing_images(query, count=10):
    """Bing 图片：a.iusc 元素的 m 属性是 JSON（murl 原图/turl 缩略/purl 来源页）。"""
    url = f"https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC2"
    soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=15).text, "html.parser")
    items = []
    for a in soup.select("a.iusc"):
        try:
            d = json.loads(a.get("m", ""))
        except Exception:
            continue
        img_url = d.get("murl") or d.get("turl")
        if not img_url:
            continue
        items.append({"title": d.get("desc", "").strip() or a.get("title", "").strip(),
                      "url": img_url, "source_page": d.get("purl", ""),
                      "width": d.get("w", 0), "height": d.get("h", 0), "engine": "bing"})
        if len(items) >= count:
            break
    return items


def baidu_images(query, count=10):
    """百度图片 acjson 接口（免 key JSON，2026-09 实测稳定）。
    data 数组含 thumbURL/middleURL/hoverURL/fromPageTitleEnc（标题）/width/height。"""
    r = requests.get("https://image.baidu.com/search/acjson",
                     params={"tn": "resultjson_com", "word": query, "pn": 0,
                             "rn": min(max(count, 15), 60), "ie": "utf-8"},
                     headers={**HEADERS, "Referer": "https://image.baidu.com/"}, timeout=15)
    r.raise_for_status()
    items = []
    for it in json.loads(r.text).get("data", []):
        if not it:
            continue
        u = it.get("middleURL") or it.get("thumbURL") or it.get("hoverURL")
        if not u:
            continue
        title = re.sub(r"<[^>]+>", "", it.get("fromPageTitleEnc") or "").strip()
        items.append({"title": title, "url": u,
                      "source_page": it.get("fromURL") or "image.baidu.com",
                      "width": it.get("width", 0), "height": it.get("height", 0),
                      "engine": "baidu"})
        if len(items) >= count:
            break
    return items


def zhipu_images(query, count=5, config=None):
    """智谱图像搜索 MCP `search_image`（0.01元/次，单次最多 5 条，无分页参数）。

    返回：标题 + 图片 URL。两点如实记录（2026-09-27 实测）：
    ① 图片 URL 是**智谱 CDN 代理链**（qc4n.bigmodel.cn/xxx_searched.png?UCloudPublicKey=
       ...&Expires=...&Signature=...），不是原始图源站；带签名有效期，适合立即查看/下载，
       不适合长期引用或作为 source_page 溯源；
    ② 无来源页字段（对比 bing/baidu 的 source_page）——要图源出处请配合
       image_search_text.py 的 bing/baidu 引擎，或对该图再做 p~p 反查。
    """
    from common import mcp_broker_call, mcp_parts_map
    parts = mcp_broker_call("image-search", "search_image", {"query": query}, config=config)
    items = []
    pending = None
    for kind, payload in mcp_parts_map(parts):
        if kind == "text":
            m = re.match(r"^(\d+)\)\s*Title:\s*(.*?);\s*Link:\s*(.*?);\s*Thumbnail:\s*(.*)$",
                         payload.strip())
            if not m:
                continue
            pending = {"title": m.group(2).strip(), "url": m.group(3).strip(),
                       "source_page": "", "width": 0, "height": 0, "engine": "zhipu"}
            items.append(pending)
        elif kind == "image" and pending is not None and not pending["url"]:
            pending["url"] = payload  # 文本行 Link 为空时用随附的 image_url 部件兜底
    items = [it for it in items if it.get("url")]
    return items[:count] if count else items


ENGINES = {"bing": bing_images, "baidu": baidu_images, "zhipu": zhipu_images}


def search_multi(query, count, order):
    """多引擎合并去重：每引擎取 count 条，按 URL 去重后截断到 count*1.5。"""
    merged, seen, used, notes = [], set(), [], []
    for name in order:
        fn = ENGINES.get(name)
        if not fn:
            notes.append(f"{name}: 未知引擎")
            continue
        try:
            items = fn(query, count)
            if items:
                used.append(name)
                for it in items:
                    key = it["url"].split("?")[0]
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(it)
            else:
                notes.append(f"{name}: 零结果")
        except Exception as e:
            notes.append(f"{name}: {type(e).__name__} {str(e)[:80]}")
    # 引擎轮询交错排列（图源多样性），而不是一引擎全占前排
    by_engine = {}
    for it in merged:
        by_engine.setdefault(it["engine"], []).append(it)
    interleaved = []
    while any(by_engine.values()):
        for eng in list(by_engine):
            if by_engine[eng]:
                interleaved.append(by_engine[eng].pop(0))
    return interleaved[:int(count * 1.5)], used, notes


def download(url, path):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser(description="文搜图：多引擎（Bing + 百度图片免 key；zhipu 智谱 MCP 按需）")
    ap.add_argument("query")
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--engine", default="bing,baidu", help="逗号分隔：bing,baidu,zhipu（zhipu=智谱 MCP，0.01元/次，最多5条）")
    ap.add_argument("--out", help="下载目录（可选）；下载前先人工挑 URL")
    args = ap.parse_args()
    config = load_config()
    count = pick(args.count, config, "default_count", default=10)
    out_dir = args.out or config.get("download_dir") or None
    order = [e.strip() for e in args.engine.split(",") if e.strip()]

    items, used, notes = search_multi(args.query, count, order)
    if not items:
        print(json.dumps({"error": "全部图片引擎失败", "attempts": notes,
                          "建议": "换关键词 / 稍后重试 / 先文搜网找图集页再补充提取"},
                         ensure_ascii=False))
        sys.exit(1)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        for i, it in enumerate(items):
            ext = os.path.splitext(it["url"].split("?")[0])[1].lower()[:5] or ".jpg"
            p = os.path.join(out_dir, f"{i+1:02d}{ext}")
            try:
                it["downloaded"] = p
                it["size"] = download(it["url"], p)
            except Exception as e:
                it["download_error"] = str(e)[:80]
    print(json.dumps({"query": args.query, "engine_used": used, "notes": notes,
                      "total": len(items), "items": items},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
