#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文搜视频（t~v）：Bing 视频 HTML 通道（免 key，国内可达）。

用法:
  python video_search_text.py "重庆洪崖洞" [--count 10] [--engine bing]
  python video_search_text.py "导数解题" --count 5 --out C:/temp  # 下载缩略图（视频本体不下载）
输出: JSON {"query","engine_used","total","items":[{title,url,thumb,source,uploader}]}
结构（2026-09-23 实测）：容器 .mc_vtvc，a.mc_vtvc_link 的 aria-label 含标题+来源+上传时间+上传人；
      mmeta 属性是 JSON 含 murl（视频/来源页真实 URL）与 turl（缩略图）。
依赖: requests+bs4。
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


def _parse_aria(aria):
    """从 aria-label 剥离标题与元信息：
    "标题 #tag 来源: xx.com · 上传时间: xx · 上传人: xx · 单击以播放"
    返回 (title, source, uploader)。"""
    t = aria
    m = re.search(r"来源:\s*([^·]+)", t)
    source = m.group(1).strip() if m else ""
    m = re.search(r"上传时间:\s*([^·]+)", t)
    upload_time = m.group(1).strip() if m else ""
    m = re.search(r"上传人:\s*([^·]+)", t)
    uploader = m.group(1).strip() if m else ""
    # 标题 = 去掉 "来源:...单击以播放" 及之后
    cut = re.search(r"(来源:|上传时间:|上传人:|单击以播放)", t)
    if cut:
        t = t[: cut.start()]
    return t.strip(), source, uploader, upload_time


def bing_videos(query, count=10):
    url = (f"https://www.bing.com/videos/search?q={quote_plus(query)}"
           f"&mkt=zh-CN&setlang=zh-hans")
    soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=15).text, "html.parser")
    items = []
    for v in soup.select(".mc_vtvc"):
        a = v.select_one("a.mc_vtvc_link")
        if not a:
            continue
        aria = a.get("aria-label", "")
        if not aria:
            continue
        title, source, uploader, upload_time = _parse_aria(aria)
        murl, turl = "", ""
        try:
            meta = json.loads(v.get("mmeta", "{}"))
            murl = meta.get("murl") or meta.get("pgurl") or ""
            turl = meta.get("turl") or ""
        except Exception:
            pass
        if not murl:
            continue
        items.append({"title": title, "url": murl, "thumb": turl,
                      "source": source, "uploader": uploader, "upload_time": upload_time})
        if len(items) >= count:
            break
    return items


ENGINES = {"bing": bing_videos}


def main():
    ap = argparse.ArgumentParser(description="文搜视频（t~v）：Bing 视频（免 key）")
    ap.add_argument("query")
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--engine", default="bing")
    ap.add_argument("--out", help="下载缩略图目录（可选）")
    args = ap.parse_args()
    config = load_config()
    count = pick(args.count, config, "default_count", default=10)

    try:
        items = ENGINES[args.engine](args.query, count)
    except KeyError:
        print(json.dumps({"error": f"未知引擎 {args.engine}"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__} {str(e)[:120]}"}, ensure_ascii=False))
        sys.exit(1)

    if not items:
        print(json.dumps({"error": "零结果", "建议": "换关键词/稍后重试"}, ensure_ascii=False))
        sys.exit(1)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for i, it in enumerate(items):
            if not it.get("thumb"):
                continue
            try:
                r = requests.get(it["thumb"], headers=HEADERS, timeout=15)
                ext = ".jpg" if "jpeg" in r.headers.get("content-type", "") else ".png"
                p = os.path.join(args.out, f"{i+1:02d}{ext}")
                open(p, "wb").write(r.content)
                it["thumb_saved"] = p
            except Exception as e:
                it["thumb_error"] = str(e)[:60]

    print(json.dumps({"query": args.query, "engine_used": [args.engine],
                      "total": len(items), "items": items},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()