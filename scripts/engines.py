#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多引擎搜索实现（两免key HTML + API/MCP 引擎）

引擎速查：
  bing_html    Bing 搜索 HTML（国内可达，自动 mkt=zh-CN）
  ddg_html     DuckDuckGo HTML（本机默认网络不可达，开梯子可用）
  baidu_html   百度搜索 HTML（国内；结构可能随改版失效，解析失败会还原返回）
  tavily       Tavily API（免费 1000 次/月；需 TAVILY_API_KEY；质量最高档）
  bailian      阿里百炼 WebSearch MCP（需 DASHSCOPE_API_KEY）
  enhanced_search  百炼 EnhancedSearch MCP（IQS 增强版，需 DASHSCOPE_API_KEY）
  zhipu        智谱 Web Search（search_std，0.01元/次；日常平价主力）
  zhipu_sogou  智谱 search_pro_sogou（0.05元/次；腾讯生态，按需）
  wiki         Wikimedia 百科词条（智谱 MCP broker，0.01元/次；单源非全网，按需）

注：sogou_html 已于 2026-09-29 移除（常态滑块验证码无法自动化，实测长期不可用）；
    搜狗识图同理移除（上传入口下线）。zhipu_sogou 是智谱 API 的搜狗索引档，与其无关。
统一返回: [{"title":.., "url":.., "snippet":..}] ；失败抛异常；缺 key 抛 KeyError。
依赖: requests + beautifulsoup4（均已装）。
"""
import json
import re
import time
from urllib.parse import quote_plus, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from common import api_key, dashscope_key, headers, normalize_url, proxies_for


# 优雅重试一次（反爬多为瞬时）
def _get(url, hdr=None, timeout=15, params=None, proxies=None):
    last = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=hdr or headers(), timeout=timeout,
                             params=params, proxies=proxies)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


# ---------- 免 key HTML 引擎 ----------

def bing_html(query, count=10, timeout=15, config=None):
    # 注意：bing 对引号严格查询常返回零结果+推荐卡片（复用b_algo类，解析即垃圾）——不加引号
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={min(max(count, 10), 50)}&mkt=zh-CN&setlang=zh-hans"
    soup = BeautifulSoup(_get(url, headers(), timeout).text, "html.parser")
    items = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        p = li.select_one("p")
        items.append({"title": a.get_text(strip=True),
                      "url": a["href"].split("&")[0],
                      "snippet": p.get_text(strip=True) if p else ""})
        if len(items) >= count:
            break
    return items


def ddg_html(query, count=10, timeout=15, config=None):
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                      headers=headers(), timeout=timeout,
                      proxies=proxies_for("ddg_html", config))
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if href.startswith("//duckduckgo.com/l/"):
            qs = parse_qs(urlparse("https:" + href).query)
            href = qs.get("uddg", [""])[0]
        if not href:
            continue
        sn = a.find_parent("div", class_="result")
        snippet = ""
        if sn:
            p = sn.select_one("a.result__snippet")
            if p:
                snippet = p.get_text(strip=True)
        items.append({"title": a.get_text(strip=True), "url": href, "snippet": snippet})
        if len(items) >= count:
            break
    return items


def baidu_html(query, count=10, timeout=15, config=None):
    """百度结果页（2026-09 Scrapling 版）：
    - 主路径 Fetcher（curl_cffi TLS 指纹）绕 requests 被降级/验证页
    - mu 属性直出真实 URL，免跳转 HEAD 还原
    - 摘要 class-free：容器纯文本剥 script/style/标签/标题（治 class 随改版失效——
      2026-08 content-right_8Zs40 → 2026-09 cos-* 设计系统两次实锤）
    - 解析失败降级 requests+bs4 手写路径"""
    # rn 保底 10：小 rn 页面被百度自家聚合卡（无 h3 的 wenda/百科卡）占满，organic 占比过低
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={min(max(count, 10), 20)}"
    items = []
    try:
        from scrapling.fetchers import Fetcher
        resp = Fetcher.get(url, stealthy_headers=True, impersonate="chrome", timeout=timeout)
        if resp.status == 200:
            for it in resp.css("div.c-container"):
                mu = (it.attrib.get("mu") or "").strip()
                if "nourl" in mu or "recommend_list" in mu:
                    continue
                h = it.css("h3 a")
                if not h:
                    continue
                title = " ".join(h[0].get_all_text(strip=True).split())
                if not title:
                    continue
                href = mu or (h[0].attrib.get("href") or "")
                if not href:
                    continue
                html = it.html_content or ""
                html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
                text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
                import html as _html
                text = _html.unescape(text)  # 剥标签后残留的 &lt; 等实体
                if text.startswith(title):
                    text = text[len(title):].strip()
                items.append({"title": title, "url": href, "snippet": text[:300]})
                if len(items) >= count:
                    break
    except ImportError:
        pass  # scrapling 未装 → 直接走兜底
    except Exception:
        items = []  # scrapling 路径任何失败 → 清空走兜底
    if not items:
        items = _baidu_html_fallback(query, count, timeout)
    # 兜底/链接还原：baidu.com/link?url= 跳转链 HEAD 跟随（mu 直出后仅兜底路径会命中）
    from concurrent.futures import ThreadPoolExecutor
    def _resolve(u):
        if "baidu.com/link?url=" not in u:
            return u
        try:
            r = requests.head(u, allow_redirects=True, timeout=3, headers=headers())
            return r.url or u
        except Exception:
            return u
    with ThreadPoolExecutor(max_workers=5) as ex:
        resolved = list(ex.map(_resolve, [it["url"] for it in items]))
    for it, u in zip(items, resolved):
        it["url"] = u
    return items


def _baidu_html_fallback(query, count=10, timeout=15):
    """requests+bs4 手写路径（scrapling 失效/未装时兜底）。"""
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={min(count, 20)}"
    soup = BeautifulSoup(_get(url, headers(), timeout).text, "html.parser")
    items = []
    for h3 in soup.select("div.result h3, h3.t"):
        a = h3.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        container = h3.find_parent("div", class_=re.compile(r"result|c-container"))
        snippet = ""
        if container:
            p = container.select_one("span[class*=content-right], div[class*=c-span-last]")
            if p:
                snippet = p.get_text(strip=True)[:300]
        items.append({"title": title, "url": a["href"], "snippet": snippet})
        if len(items) >= count:
            break
    return items


# ---------- API 引擎 ----------

def tavily(query, count=10, timeout=20, key=None, config=None, days=None):
    """Tavily API：质量最高，支持 include_domains / topic / search_depth / time_range。"""
    key = key or api_key("tavily")
    if not key:
        raise KeyError("TAVILY_API_KEY 未配置")
    tcfg = (config or {}).get("tavily") or {}
    body = {"query": query, "max_results": min(count, 20),
            "search_depth": tcfg.get("search_depth", "basic"),
            "include_answer": False, "include_raw_content": False}
    tr = _recency_bucket(days, {"day": 1, "week": 7, "month": 31, "year": 365})
    if tr:
        body["time_range"] = tr  # tavily 原生时间过滤：day/week/month/year
    base = (tcfg.get("api_base") or "https://api.tavily.com").rstrip("/")
    r = requests.post(f"{base}/search", json=body,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      timeout=timeout)
    r.raise_for_status()
    data = r.json()
    out = []
    for it in data.get("results") or []:
        out.append({"title": it.get("title", ""), "url": it.get("url", ""),
                    "snippet": it.get("content", "")[:400]})
    return out


def bailian(query, count=10, timeout=25, key=None, config=None, days=None):
    """阿里百炼 WebSearch MCP（stateless HTTP）：国内通道，中文质量高。
    接口无时间过滤参数——--days 对本引擎是 no-op（上层如实标注）。"""
    key = key or dashscope_key(config)
    if not key:
        raise KeyError("DASHSCOPE_API_KEY 未配置")
    base = ((config or {}).get("dashscope") or {}).get("mcp_base") or "https://dashscope.aliyuncs.com/api/v1/mcps"
    url = f"{base}/WebSearch/mcp"
    hdr = {"Authorization": key, "Content-Type": "application/json"}

    def _post(payload):
        r = requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers=hdr, timeout=timeout)
        r.raise_for_status()
        return r.json()

    _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                      "clientInfo": {"name": "advanced-web-search", "version": "2.0"}}})
    resp = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "bailian_web_search",
                             "arguments": {"query": query, "count": min(count, 10)}}})
    if resp.get("error"):
        raise RuntimeError(f"MCP error: {str(resp['error'])[:120]}")
    pages = json.loads(resp["result"]["content"][0]["text"]).get("pages", [])
    return [{"title": p.get("title", ""), "url": p.get("url", ""),
             "snippet": p.get("snippet", "")} for p in pages]


def enhanced_search(query, count=10, timeout=30, config=None, key=None, days=None):
    """百炼 EnhancedSearch MCP（search_pro）：IQS 增强版搜索引擎，权威站点优先召回。
    接口仅 query 参数（additionalProperties=false，无 count），固定返回 10 条
    （官方宣称召回 40-80 后精选）。字段与 bailian 基础版同构。2026-09-13 实测接入。
    接口无时间过滤参数——--days 对本引擎是 no-op（上层如实标注）。"""
    key = key or dashscope_key(config)
    if not key:
        raise KeyError("DASHSCOPE_API_KEY 未配置")
    base = ((config or {}).get("dashscope") or {}).get("mcp_base") or "https://dashscope.aliyuncs.com/api/v1/mcps"
    url = f"{base}/EnhancedSearch/mcp"
    hdr = {"Authorization": key, "Content-Type": "application/json"}

    def _post(payload):
        r = requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers=hdr, timeout=timeout)
        r.raise_for_status()
        return r.json()

    _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                      "clientInfo": {"name": "advanced-web-search", "version": "2.1"}}})
    resp = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "search_pro",
                             "arguments": {"query": query[:500]}}})  # query 上限 500 字
    if resp.get("error"):
        raise RuntimeError(f"MCP error: {str(resp['error'])[:120]}")
    pages = json.loads(resp["result"]["content"][0]["text"]).get("pages", [])
    return [{"title": p.get("title", ""), "url": p.get("url", ""),
             "snippet": p.get("snippet", "")} for p in pages]



def _recency_bucket(days, buckets):
    """--days N → 引擎原生时间档。buckets: {档名: 天数上限}，取第一个 N<=上限 的档；超档/无 N 返回 None。"""
    if not days:
        return None
    for name, cap in buckets.items():
        if days <= cap:
            return name
    return None


def _zhipu_call(query, count, timeout, key, search_engine, recency=None,
                domain=None, content_size=None, config=None):
    """智谱 Web Search API 通用调用。
    实测坑（2026-09-23）：
    - `search_intent` 是必需字段且 **true 会导致 0 条**（意图识别拦截建议类查询）→ 恒传 false
    - count 不按传入值精确返回（实测请求 10 返回 15/50；sogou 档文档称枚举 10-50 且
      同时指定 domain+recency 时不生效）→ 客户端截断兜底
    - 部分查询 link 全空/畸形（已过滤）
    - search_recency_filter: oneDay/oneWeek/oneMonth/oneYear/noLimit（全档支持）
    - search_domain_filter: 白名单域名（std/pro/sogou 支持；实测量化正常）
    - content_size: medium（摘要）/ high（长正文）（全档支持）"""
    body = {"search_engine": search_engine, "search_query": query[:70],
            "search_intent": False, "count": min(max(count, 1), 50)}
    if recency:
        body["search_recency_filter"] = recency
    if domain:
        body["search_domain_filter"] = domain
    if content_size:
        body["content_size"] = content_size
    base = ((config or {}).get("zhipu") or {}).get("api_base") or "https://open.bigmodel.cn/api/paas/v4/web_search"
    r = requests.post(base, json=body,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      timeout=timeout)
    r.raise_for_status()
    items = []
    for it in r.json().get("search_result") or []:
        link = it.get("link") or ""
        # 实测坑：①个别条目 url 空 ②url 有邮箱等畸形值（xurx@eol.cn）→ 只收 http(s)
        # 2026-09-29 新发现：**开启 search_recency_filter 后服务端系统性返回 link=""**
        # （裸测 oneWeek/oneMonth 各 10 条原始结果全部无链，noLimit 正常）——此时收
        # 无链条目并标 url_missing=True，让 --days 仍能拿到"最近有什么"的标题+日期，
        # URL 缺失由上层如实呈现（用标题回搜即可溯源）。
        if link.startswith(("http://", "https://")):
            items.append({"title": it.get("title", ""), "url": link,
                          "snippet": it.get("content", ""), "media": it.get("media", ""),
                          "publish_date": it.get("publish_date", "")})
        elif recency:
            items.append({"title": it.get("title", ""), "url": "",
                          "url_missing": True, "snippet": it.get("content", ""),
                          "media": it.get("media", ""), "publish_date": it.get("publish_date", "")})
        else:
            continue
        if len(items) >= count:  # 实测坑：count 参数无效，服务端超发（请求10返回50）→ 客户端截断
            break
    return items


def zhipu(query, count=10, timeout=25, config=None, key=None, days=None):
    """智谱 Web Search（search_std，0.01元/次——日常查询平价主力，count 1-50）。
    days：--days N 原生时间过滤（oneDay/oneWeek/oneMonth/oneYear/noLimit 自动取档）。"""
    key = key or api_key("zhipu", config)
    if not key:
        raise KeyError("ZHIPU_API_KEY / secrets.json['zhipu'] 未配置")
    zcfg = (config or {}).get("zhipu") or {}
    engine_name = zcfg.get("search_engine", "search_std")
    recency = _recency_bucket(days, {"oneDay": 1, "oneWeek": 7, "oneMonth": 31, "oneYear": 365})
    return _zhipu_call(query, count, timeout, key, engine_name, recency=recency, config=config)


def zhipu_sogou(query, count=10, timeout=25, config=None, key=None, days=None):
    """智谱 search_pro_sogou（0.05元/次）：覆盖知乎+腾讯生态（企鹅号/新闻）——内容面补位。"""
    key = key or api_key("zhipu", config)
    if not key:
        raise KeyError("ZHIPU_API_KEY / secrets.json['zhipu'] 未配置")
    recency = _recency_bucket(days, {"oneDay": 1, "oneWeek": 7, "oneMonth": 31, "oneYear": 365})
    return _zhipu_call(query, count, timeout, key, "search_pro_sogou", recency=recency, config=config)


def wiki(query, count=5, timeout=90, config=None):
    """Wikimedia 百科（智谱 MCP broker `wiki` 服务，tool=wikipediaSearch，0.01元/次）。

    返回百科词条（标题 + 正文摘要 + 词条 URL），**不是通用网页检索**：
    200+ 语言，适合"某概念/事件/人物是什么"的定名与背景；不适合时效性/政策类查询
    （需要最新网页时用 zhipu/search_std 或 --fuse）。
    截至 2026-09-27 实测：中英文查询均可（英文词条返回中文条目），articleBody 完整；
    **服务在智谱侧，无需梯子**（源站被墙不影响本通道）。
    ⚠️ 两点如实记录：①命中偏窄且排序偶有不准（查"高考"首位返回"俄罗斯高考"、
      "Newton"返回"曼联"）——适合已知词条名的定名/背景核实，不适合开放式检索；
    ②少数条目无 detailedDescription，此时 url 落 result.url（词条官方站，非维基页）。
    结构：{"status":200,"msg":[{"resultScore":N,"result":{name, description,
          url, detailedDescription:{articleBody, url}}}]}
    """
    from common import mcp_broker_call, mcp_json
    parts = mcp_broker_call("wiki", "wikipediaSearch", {"src": query},
                            timeout=timeout, config=config)
    data = mcp_json(parts[0]) if parts else None
    items = []
    for it in (data or {}).get("msg") or []:
        r = it.get("result") or {}
        dd = r.get("detailedDescription") or {}
        body = dd.get("articleBody") or r.get("description") or ""
        url = dd.get("url") or r.get("url") or ""
        if not (r.get("name") or body or url):
            continue
        entry = {"title": r.get("name", ""), "url": url, "snippet": body[:400],
                 "wiki_type": r.get("description", ""),
                 "score": it.get("resultScore")}
        if r.get("url") and r["url"] != url:
            entry["official_url"] = r["url"]  # 词条官方站（维基 url 之外的第二字段）
        items.append(entry)
        if len(items) >= count:
            break
    return items


ENGINES = {"bing_html": bing_html, "ddg_html": ddg_html, "baidu_html": baidu_html,
           "tavily": tavily, "bailian": bailian,
           "enhanced_search": enhanced_search,
           "zhipu": zhipu, "zhipu_sogou": zhipu_sogou, "wiki": wiki}

# 原生支持时间过滤（--days）的引擎；名单外的引擎收到 days 也不支持，search.py 如实标注
TIME_CAPABLE = {"zhipu", "zhipu_sogou", "tavily"}

# 引擎可达性备注（写入结果便于排障）
NOTES = {
    "ddg_html": "默认网络被墙，需梯子（vpn_engines 分流自动走代理）",
    "tavily": "免费 1000 次/月，需 TAVILY_API_KEY；索引为 AI 检索调优，官方页命中率最高",
    "bailian": "需 DASHSCOPE_API_KEY；国内直连，中文质量高",
    "enhanced_search": "需 DASHSCOPE_API_KEY；IQS 增强版，权威站点优先；固定 10 条无 count 参数；按次计费（额度以百炼控制台为准）",
    "zhipu": "需智谱 key（secrets.json zhipu）；search_std 0.01元/次，count 1-50，返回 publish_date/media，支持域名+时间过滤。⚠️ 2026-09-29 裸测：**开 search_recency_filter 后服务端 link 字段全空**（10/10），引擎此时返回 url_missing=True 的无链条目（标题+日期可用）",
    "zhipu_sogou": "需智谱 key；search_pro_sogou 0.05元/次，覆盖知乎+腾讯生态。recency 模式同 zhipu（link 全空→url_missing）",
    "wiki": "需智谱 key；Wikimedia 百科词条（0.01元/次，单源非全网检索），按需 --engine wiki，不进默认池",
}
