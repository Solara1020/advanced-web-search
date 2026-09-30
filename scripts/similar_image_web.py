#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图搜图 · 联网多引擎：智谱 MCP 反向图搜 / 百度识图 / Yandex 逐级尝试。

用法:
  python similar_image_web.py 本地图片路径 [--limit 10] [--engine baidu_image,yandex]
  python similar_image_web.py "https://公网图.png" [--engine zhipu_image]        # 图 URL 才走智谱
  python similar_image_web.py "https://公网图.png" --engine zhipu_zoom --bbox "[380,300,620,700]" --ref-name "教学楼"
输出: JSON {used_engine, items:[{title,url,source_page,kind,engine}], attempts:[...]}
      智谱通道额外给一条 kind=visual_meta 项（best_guess_labels + visual_entities 置信分）。
引擎分工:
  baidu_image   本地文件（Playwright 上传，免费但约 8s）
  zhipu_*       公网图片 URL（智谱 MCP，0.01元/次，快；本地文件与 data:URI 一律不支持）
  yandex        需梯子、入口易改版
  （sogou 搜狗识图已于 2026-09-29 移除：上传入口下线，实测长期不可用）
失败: 全部引擎不可用 → {error, attempts, 降级链}（exit 1）
依赖: requests；playwright（百度识图/Yandex 渲染）；智谱 key（secrets.json['zhipu']）
"""
import argparse
import json
import re
import sys

import requests

from common import load_config, headers as common_headers, UA

TIMEOUT = 20


def _session():
    s = requests.Session()
    s.headers.update(common_headers())
    return s


def _browser_image_search(image_path, homepage, patterns, limit, wait_ms=4500):
    """SPA 页面（识图结果页全 JS 渲染）用 Playwright 真浏览器：打开页面→上传→等渲染→抓结果图。

    patterns: 结果图 URL 正则列表（只认第一组捕获）。
    返回: 图片 URL 列表；页面改版无上传入口 → RuntimeError。
    """
    from playwright.sync_api import sync_playwright
    out, seen = [], set()
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, channel="chrome")  # 系统 Chrome
        except Exception:
            b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1280, "height": 1000}, user_agent=UA)
        try:
            pg.goto(homepage, wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(1500)
            target = None
            for sel in ('input[type="file"]', 'input[accept*="image"]',
                        'input[name*="picture" i]', 'input[id*="pic" i]'):
                if pg.query_selector_all(sel):
                    target = sel
                    break
            if not target:
                raise RuntimeError("页面无上传入口（改版）")
            pg.set_input_files(target, image_path)
            pg.wait_for_timeout(wait_ms)
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            html = pg.content()
            b.close()
        except Exception:
            b.close()
            raise
    for pat in patterns:
        for m in re.finditer(pat, html):
            u = m.group(1).replace("\\/", "/").replace("\\u002F", "/")
            u = re.split(r'[&\\"\']+', u)[0]
            if any(d in u.lower() for d in ("logo", "favicon", "icon")):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
            if len(out) >= limit:
                return out
    return out


def baidu_graph_search(image_path, limit, timeout=TIMEOUT):
    """百度识图（Playwright 完整上传交互版，2026-09-13 实测突破）：
    打开 graph.baidu.com → set_input_files 上传 → 监听 /ajax/pcsimi 结构化 JSON（相似图 30 条）
    + 页面文本抓"图片来源"（相似来源页标题/站点）。
    旧 upload API 撞 JS token（Params illegal），本路径绕过。"""
    from playwright.sync_api import sync_playwright
    import json as _json
    api_payloads = []
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True, channel="chrome")  # 系统 Chrome（免下 chromium build）
        except Exception:
            b = pw.chromium.launch(headless=True)
        pg = b.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})

        def _on_resp(resp):
            if "/ajax/pcsimi" in resp.url:
                try:
                    api_payloads.append(resp.text())
                except Exception:
                    pass

        pg.on("response", _on_resp)
        try:
            pg.goto("https://graph.baidu.com/pcpage/index?tpl_from=pc",
                    wait_until="domcontentloaded", timeout=timeout * 1000)
            pg.wait_for_timeout(1500)
            pg.set_input_files("input[type=file]", image_path)  # 完整上传交互
            pg.wait_for_timeout(8000)  # 等结果接口返回
            body_text = pg.inner_text("body")[:3000]  # "图片来源"等 DOM 文本
        finally:
            b.close()
    items = []
    for payload in api_payloads:
        try:
            data = _json.loads(payload).get("data") or {}
            for it in data.get("list") or []:
                u = it.get("thumbUrl") or it.get("imageUrl") or ""
                if not u:
                    continue
                items.append({"title": "", "url": u, "size": f'{it.get("width","?")}x{it.get("height","?")}',
                              "source_page": "graph.baidu.com"})
                if len(items) >= limit:
                    return items
        except Exception:
            continue
    if not items and "图片来源" in body_text:
        # 接口未捕获但页面有来源区块：抓来源页链接兜底
        for m in re.finditer(r"(https?://[^\s]+)", body_text):
            items.append({"title": "", "url": m.group(1), "source_page": "graph.baidu.com(dom)"})
            if len(items) >= limit:
                break
    return items


def baidu_image_search(image_path, limit, timeout=TIMEOUT):
    """百度识图旧路径（浏览器渲染+正则，历史保留作兜底）。"""
    return _browser_image_search(
        image_path,
        "https://graph.baidu.com/s?carousel=0&entrance=GENERAL",
        [r'https?://himg\.bdimg\.com/\S+\.(?:jpg|jpeg|png|webp)',
         r'https?://gimg\.baidu\.com/\S+\.(?:jpg|jpeg|png|webp)',
         r'https?://img[0-9]?\.[a-z]+\.bdimg\.com/\S+'],
        limit)


def yandex_search_rendered(image_path, limit):
    """Yandex 识图浏览器渲染版（requests 版网络不稳时的同引擎兜底）。"""
    patterns = [r'https?://avatars\.mds\.yandex\.net/(?:get-images-thumbs|get-content)/\S+']
    home = "https://yandex.com/images/search?rpt=imageview"
    urls = _browser_image_search(image_path, home, patterns, limit, wait_ms=6000)
    return [{"title": "", "url": u, "source_page": "yandex(rendered)"} for u in urls]


def yandex_search(image_path, limit, timeout=TIMEOUT):
    """Yandex CBIR：上传图片 → 302 带 cbir_id → 结果页解析相似图。"""
    s = _session()
    s.get("https://yandex.com/images/search", timeout=timeout)  # 拿 cookie
    with open(image_path, "rb") as f:
        files = {"upfile": (image_path.split("/")[-1] or "query.jpg", f, "image/jpeg")}
        r = s.post("https://yandex.com/images/search?rpt=imageview", files=files,
                   allow_redirects=False, timeout=timeout)
    if r.status_code in (301, 302, 303) and r.headers.get("Location"):
        page_url = "https://yandex.com/images/search" + r.headers["Location"].split("/images/search")[-1]
    else:
        # 可能直接 200 返回结果页
        page_url = r.url
    page = s.get("https://yandex.com/images/search" + page_url.split("/images/search")[-1],
                 timeout=timeout).text
    items = _extract_images(page, limit, page_url=http_url(page_url))
    if items:
        return items
    # requests 通道未出结果（网络中断/JS 渲染）→ 浏览器渲染兜底
    return yandex_search_rendered(image_path, limit)


def http_url(u):
    return u if u.startswith("http") else "https://yandex.com/images/search" + u


def _extract_images(html, limit, page_url=None):
    """只认 Yandex 图搜相似图特征 URL（get-images-thumbs / get-content），
    杜绝把页面 JS JSON 里的 UI 数据当作结果。"""
    urls = []
    # 1) img 标签特征路径
    for m in re.finditer(r'(?:src|data-src)="(https?://avatars\.mds\.yandex\.net/(?:get-images-thumbs|get-content)[^"]*)"', html):
        u = m.group(1).replace("\\/", "/").replace("\\u002F", "/")
        if u not in urls:
            urls.append(u)
        if len(urls) >= limit:
            break
    # 2) JSON 内 thumb 字段
    if len(urls) < limit:
        for m in re.finditer(r'"(?:thumb|thumbUrl|previewUrl)"\s*:\s*"([^"]+)"', html):
            u = m.group(1).replace("\\/", "/").replace("\\u002F", "/")
            if "get-images-thumbs" not in u:
                continue
            if u not in urls:
                urls.append(u)
            if len(urls) >= limit:
                break
    if urls:
        return [{"title": "", "url": u, "source_page": "yandex"} for u in urls]
    # 3) Playwright 渲染兜底（JS 异步加载相似图）
    rendered = _rendered_image_urls(limit, page_url)
    if rendered:
        return [{"title": "", "url": u, "source_page": "yandex(rendered)"} for u in rendered]
    return []


def _rendered_image_urls(limit, page_url=None):
    """用 Playwright 在 Yandex 识图结果页等 networkidle 后抓相似图（兜底，需 playwright 已装）。"""
    try:
        from playwright.sync_api import sync_playwright
        pats = re.compile(r"https?://avatars\.mds\.yandex\.net/(?:get-images-thumbs|get-content)[^\"']*")
        out = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900},
                                    user_agent=UA)
            target = page_url or "https://yandex.com/images/search?rpt=imageview"
            try:
                page.goto(target, wait_until="networkidle", timeout=25000)
            except Exception:
                page.goto(target, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
        for m in pats.finditer(html):
            u = m.group(0).replace("\\u002F", "/")
            if u not in out:
                out.append(u)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _is_url(u):
    return isinstance(u, str) and u.startswith(("http://", "https://"))


# ---------- 智谱图像搜索 MCP（2026-09-27 接入，均 0.01 元/次） ----------
# 与本地文件引擎的根本差别：**入参是图片 URL，不是文件**。服务端会先对 URL 发 HEAD
# 校验（10s 超时即拒），因此：本地文件不支持（data:URI 实测被拒）、被墙站点图源
# （upload.wikimedia.org 实测超时）也不可用——只有公网可达的图 URL 能走通。
# 返回结构为 Google-Lens 系：full/partial/visually_similar_images（相似图）、
# pages_with_matching_images（含图网页）、web_entities（视觉实体+置信分）、
# best_guess_labels（最佳猜测标签）。截图为 2026-09-27 实测字段全表。

def _zhipu_image_items(parts, limit, source):
    """把智谱图搜工具返回解析成统一 items（相似图/网页/视觉标签三类混排）。"""
    from common import mcp_json
    data = mcp_json(parts[0]) if parts else None
    if not data:
        return []
    items = []
    for key, kind in (("full_matching_images", "完全匹配图"),
                      ("partial_matching_images", "部分匹配图"),
                      ("visually_similar_images", "视觉相似图")):
        for it in data.get(key) or []:
            u = (it or {}).get("url") if isinstance(it, dict) else None
            if u:
                items.append({"title": "", "url": u, "source_page": "", "kind": kind,
                              "engine": source})
    for it in data.get("pages_with_matching_images") or []:
        if not isinstance(it, dict) or not it.get("url"):
            continue
        items.append({"title": it.get("pageTitle", ""), "url": it["url"],
                      "source_page": "pages_with_matching_images", "kind": "含图网页",
                      "engine": source})
    labels = [l.get("label", "") for l in data.get("best_guess_labels") or []
              if isinstance(l, dict) and l.get("label")]
    ents = [{"description": e.get("description", ""), "score": round(float(e.get("score") or 0), 3)}
            for e in data.get("web_entities") or [] if isinstance(e, dict) and e.get("description")]
    if labels or ents:
        # 视觉识别结论作为首条 meta 项返回（不是图片，url 为空，便于调用方单独取用）
        items.insert(0, {"kind": "visual_meta", "title": "；".join(labels), "url": "",
                         "source_page": "", "best_guess_labels": labels,
                         "visual_entities": ents[:5], "engine": source})
    return items[:limit] if limit else items


def zhipu_image_search(image_ref, limit, config=None, timeout=90):
    """智谱图像搜索 MCP `image_search_with_image`（反向图搜，0.01元/次）。
    需公网图片 URL（见上方说明）；本地文件会明确报错而不是静默失败。"""
    if not _is_url(image_ref):
        raise RuntimeError("智谱图搜图只吃公网图片 URL（本地文件请用 baidu_image 或本地 phash）")
    from common import mcp_broker_call
    parts = mcp_broker_call("image-search", "image_search_with_image",
                            {"img_url": image_ref}, timeout=timeout, config=config)
    return _zhipu_image_items(parts, limit, "zhipu_image")


def zhipu_pages_search(image_ref, limit, config=None, timeout=90):
    """智谱图像搜索 MCP `text_search_with_image`（图所在网页 + 视觉相似图 + 标签，0.01元/次）。
    用途偏"这张图出自哪里"的溯源，返回以 pages_with_matching_images 为主。"""
    if not _is_url(image_ref):
        raise RuntimeError("智谱图搜图只吃公网图片 URL（本地文件请用 baidu_image 或本地 phash）")
    from common import mcp_broker_call
    parts = mcp_broker_call("image-search", "text_search_with_image",
                            {"img_url": image_ref}, timeout=timeout, config=config)
    return _zhipu_image_items(parts, limit, "zhipu_pages")


def zhipu_zoom_search(image_ref, limit, bbox=None, ref_name=None, config=None, timeout=90):
    """智谱图像搜索 MCP `image_zoom_in_search_tool`（按 bbox 裁剪区域后再搜，0.01元/次）。
    bbox 格式 '[x1,y1,x2,y2]'，取值 0-999（相对坐标）；ref_name 是框内物体的名字（可选）。
    用途：整图搜不到时，框住局部（如题目里的图/标识）再搜。"""
    if not _is_url(image_ref):
        raise RuntimeError("智谱图搜图只吃公网图片 URL（本地文件请用 baidu_image 或本地 phash）")
    if not bbox:
        raise RuntimeError("zhipu_zoom 需要 --bbox '[x1,y1,x2,y2]'（0-999 相对坐标）")
    args = {"img_url": image_ref, "bbox": bbox}
    if ref_name:
        args["ref_name"] = ref_name
    from common import mcp_broker_call
    parts = mcp_broker_call("image-search", "image_zoom_in_search_tool",
                            args, timeout=timeout, config=config)
    return _zhipu_image_items(parts, limit, "zhipu_zoom")


ENGINES = {"yandex": yandex_search, "baidu_image": baidu_graph_search,
           "baidu_image_legacy": baidu_image_search,
           "zhipu_image": zhipu_image_search, "zhipu_pages": zhipu_pages_search,
           "zhipu_zoom": zhipu_zoom_search}

# 需要公网图片 URL 的引擎（本地文件输入时自动跳过，不浪费重试）
URL_ONLY_ENGINES = {"zhipu_image", "zhipu_pages", "zhipu_zoom"}


def _with_retry(fn, *args, retries=2, pause=2, **kw):
    last = None
    for i in range(retries + 1):
        try:
            return fn(*args, **kw)
        except Exception as e:
            last = e
            if i < retries:
                import time
                time.sleep(pause)
    raise last


def main():
    ap = argparse.ArgumentParser(
        description="图搜图联网多引擎（百度识图 Playwright版/Yandex/智谱 MCP 反向图搜）")
    ap.add_argument("image", help="本地图片路径，或公网图片 URL（URL 才走 zhipu_* 引擎）")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--engine", default=None, help="逗号分隔引擎名（默认 config.image_engines）")
    ap.add_argument("--bbox", default=None, help="zhipu_zoom 用：'[x1,y1,x2,y2]' 0-999 相对坐标")
    ap.add_argument("--ref-name", default=None, help="zhipu_zoom 用：框内物体名（可选）")
    args = ap.parse_args()
    config = load_config()
    order = ([e.strip() for e in args.engine.split(",")] if args.engine
             else config.get("image_engines") or ["zhipu_image", "baidu_image", "yandex"])

    attempts = []
    is_url = _is_url(args.image)
    for eng in order:
        fn = ENGINES.get(eng)
        if not fn:
            attempts.append(f"{eng}: 未知引擎")
            continue
        if eng in URL_ONLY_ENGINES and not is_url:
            attempts.append(f"{eng}: 跳过（需公网图片 URL，当前是本地文件）")
            continue
        extra = {}
        if eng in URL_ONLY_ENGINES:
            extra["config"] = config
            if eng == "zhipu_zoom":
                if not args.bbox:
                    attempts.append(f"{eng}: 跳过（需 --bbox '[x1,y1,x2,y2]'）")
                    continue
                extra["bbox"] = args.bbox
                extra["ref_name"] = args.ref_name
        try:
            items = _with_retry(fn, args.image, args.limit, **extra)
            if items:
                print(json.dumps({"used_engine": eng, "input": args.image, "items": items,
                                  "attempts": attempts}, ensure_ascii=False, indent=2))
                return
            attempts.append(f"{eng}: 零结果")
        except Exception as e:
            attempts.append(f"{eng}: {type(e).__name__} {str(e)[:100]}")
    print(json.dumps({
        "error": "联网以图搜图全部引擎不可用",
        "input": args.image,
        "attempts": attempts,
        "降级链": "1) 图搜文(OCR)-->文搜图（稳定） 2) similar_image_local.py 本地 dhash/phash（稳定）"
                  " 3) 本地图想用智谱 API：先把图传到任意公网图床拿 URL 再 --engine zhipu_image",
    }, ensure_ascii=False))
    sys.exit(1)


if __name__ == "__main__":
    main()
