#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站站内数据适配器（platforms）：免签名公开接口先行，解决平台站内数据结构性缺口
（search.py 只能拿二手文章，粉丝数/播放量必须直连平台接口）。

用法:
  python bilibili.py card <mid|空间URL>       # UP主信息：昵称/签名/粉丝数/投稿数/等级
  python bilibili.py stat <mid|空间URL>       # 关系数据：粉丝/关注/悄悄关注（relation/stat）
  python bilibili.py view <BV号|视频URL>       # 单视频：播放/点赞/投币/收藏/分享/弹幕/评论/时长/竖横屏
  python bilibili.py search <关键词> [--count N]  # 站内搜索（⚠️ 需 wbi 签名+风控 cookie，未实现，
                                              # 实测会返回 -412；替代方案：bing/baidu 加 site:bilibili.com）

输出: JSON（stdout）。失败输出 {"error":...} 并 exit 1。
依赖: requests。headers 需 UA+Referer，否则部分接口返回 412/-352。
已知边界（2026-09-12 实测口径）:
  - card/stat/view 免签名可直接调用，但高频调用会触发风控（-352/-412），串行+间隔使用
  - space 投稿列表 /x/space/wbi/arc/search 与站内搜索需 wbi 签名，本脚本不实现
"""
import argparse
import json
import re
import sys
import time

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _headers():
    return {"User-Agent": _UA, "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*"}


_SESSION = None


def _session():
    """复用会话；首用前访问主站拿 buvid3 等风控 cookie（缺它 view 接口实测 412）。"""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(_headers())
        try:
            _SESSION.get("https://www.bilibili.com/", timeout=10)
        except Exception:
            pass  # 拿不到 cookie 也继续试，失败由调用方报错
    return _SESSION


def _get_json(url, params=None, timeout=15):
    r = _session().get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站API code={data.get('code')} msg={data.get('message', '')[:80]}")
    return data.get("data") or {}


def _mid_of(arg):
    """从纯数字 mid 或空间 URL（space.bilibili.com/123）提取 mid。"""
    arg = arg.strip()
    if arg.isdigit():
        return arg
    m = re.search(r"space\.bilibili\.com/(\d+)", arg)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 {arg!r} 解析 mid（应为纯数字 UID 或 space.bilibili.com/<uid>）")


def _bvid_of(arg):
    """从 BV 号或视频 URL 提取 BV 号（支持 av 号）。"""
    arg = arg.strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", arg)
    if m:
        return {"bvid": m.group(1)}
    m = re.search(r"av(\d+)", arg, re.I)
    if m:
        return {"aid": m.group(1)}
    raise ValueError(f"无法从 {arg!r} 解析 BV/av 号")


def _fmt_duration(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def card(arg):
    d = _get_json("https://api.bilibili.com/x/web-interface/card",
                  params={"mid": _mid_of(arg), "photo": "true"})
    c = d.get("card") or {}
    return {"mid": c.get("mid"), "name": c.get("name"), "sign": (c.get("sign") or "").strip(),
            "fans": c.get("fans"), "archive_count": d.get("archive_count"),  # 在 data 顶层
            "follower": d.get("follower"),
            "level": (c.get("level_info") or {}).get("current_level"),
            "official_type": ((c.get("official_verify") or {}).get("type")),  # -1无 0个人 1机构
            "vip_type": c.get("vipType")}


def stat(arg):
    d = _get_json("https://api.bilibili.com/x/relation/stat", params={"vmid": _mid_of(arg)})
    return {"mid": d.get("mid"), "following": d.get("following"),
            "follower": d.get("follower"),  # 粉丝数（主指标）
            "whisper": d.get("whisper"), "black": d.get("black")}


def _fmt_view(d):
    """videoData/API data → 统一输出格式（API 与 Playwright 两路共用）。"""
    s = d.get("stat") or {}
    dim = d.get("dimension") or {}
    w, h = dim.get("width") or 0, dim.get("height") or 0
    orient = ("landscape" if w >= h else "portrait") if (w and h) else "unknown"
    owner = d.get("owner") or {}
    return {"bvid": d.get("bvid"), "aid": d.get("aid"), "title": d.get("title"),
            "owner": {"mid": owner.get("mid"), "name": owner.get("name")},
            "pubdate": d.get("pubdate"),
            "duration_sec": d.get("duration"), "duration": _fmt_duration(d.get("duration")),
            "videos": d.get("videos"),  # 分P数
            "dimension": dim, "orientation": orient,
            "via": d.pop("_via", "api"),
            "stat": {"view": s.get("view"), "like": s.get("like"),
                     "coin": s.get("coin"), "favorite": s.get("favorite"),
                     "share": s.get("share"), "danmaku": s.get("danmaku"),
                     "reply": s.get("reply")}}  # reply=评论数


def _view_via_playwright(bvid):
    """API 被风控（412）时的兜底：headless 渲染视频页读 __INITIAL_STATE__.videoData。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, channel="chrome")  # 系统 Chrome（免下 chromium build）
        except Exception:
            b = p.chromium.launch(headless=True)
        page = b.new_page(user_agent=_UA, viewport={"width": 1280, "height": 800})
        try:
            page.goto(f"https://www.bilibili.com/video/{bvid}",
                      wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)  # 等前端注入状态
            raw = page.evaluate(
                "() => { const s = window.__INITIAL_STATE__; "
                "return (s && s.videoData) ? JSON.stringify(s.videoData) : '' }")
        finally:
            b.close()
    if not raw:
        raise RuntimeError("Playwright 渲染完成但未取到 __INITIAL_STATE__.videoData（视频可能不存在/仅限地区）")
    d = json.loads(raw)
    d["_via"] = "playwright"
    return d


def view(arg):
    target = _bvid_of(arg)
    try:
        d = _get_json("https://api.bilibili.com/x/web-interface/view", params=target)
    except Exception as e:
        # 412/-352 风控 → Playwright 兜底（仅支持 bvid；av 号先换算失败则原样报错）
        bvid = target.get("bvid")
        if not bvid:
            raise
        try:
            return _fmt_view(_view_via_playwright(bvid))
        except Exception:
            raise e
    return _fmt_view(d)


def search(keyword, count=10):
    raise NotImplementedError(
        "B站站内搜索需 wbi 签名+风控cookie（实测返回 -412），未实现；"
        "替代：search.py \"<关键词> site:bilibili.com\" 或直接用 card/view 接口")


def main():
    ap = argparse.ArgumentParser(description="B站站内数据适配器（card/stat/view 免签名）")
    ap.add_argument("action", choices=["card", "stat", "view", "search"])
    ap.add_argument("target", help="mid/空间URL 或 BV号/视频URL 或 关键词")
    ap.add_argument("--count", type=int, default=10)
    args = ap.parse_args()
    try:
        time.sleep(0.3)  # 轻间隔降风控概率
        if args.action == "card":
            out = card(args.target)
        elif args.action == "stat":
            out = stat(args.target)
        elif args.action == "view":
            out = view(args.target)
        else:
            out = search(args.target, args.count)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    except NotImplementedError as e:
        print(json.dumps({"error": "not_implemented", "detail": str(e)}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__} {str(e)[:150]}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
