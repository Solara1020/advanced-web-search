#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多引擎搜索编排：按 config 引擎优先级逐级降级，跨引擎合并去重，共识检测/广告过滤/时间过滤/缓存可选。

用法:
  python search.py "查询词"                    # auto：config.engine_priority 顺序
  python search.py "查询词" --engine tavily,bing_html   # 指定引擎（按序）
  python search.py "查询词" --count 20 --rerank          # 结果按查询相关度重排（DMXAPI向量）
  python search.py "查询词" --cross-verify 3 --engine bing_html,baidu_html,zhipu   # 共识检测
  python search.py "查询词" --days 7 --engine zhipu      # 原生时间过滤（zhipu/tavily 支持）
  python search.py "查询词" --no-ads           # 丢弃疑似招生广告/软文（默认只打标下沉）
  python search.py "查询词" --no-cache         # 跳过查询缓存（默认 TTL 6h，命中标 cache 字段）
  python search.py "查询词" --embed                      # 结果附加向量（供后续聚类/去重）
输出: JSON {query, engine_used, items:[{title,url,snippet,engine,engines,consensus,ad_suspect}],
           relevant_count, warning, cross_verify, time_filter, ad_filter, quality_notes, cache, skipped}
依赖: engines.py + common.py + cache.py（requests/bs4；sqlite3 为标准库）
"""
import argparse
import json
import re
import sys

from common import load_config, normalize_url
import engines



def relevance_gate(items, query, min_overlap=1):
    """相关性守门：标题+摘要与查询的字符级重叠不足则剔除。
    治"猜你想搜"推荐卡/离题垃圾（bing 实测踩坑）。中文按 2 字滑窗，英文按词。"""
    q = query.replace('"', "").replace("'", "")
    q_cn = set(q[i:i+2] for i in range(len(q) - 1) if "一" <= q[i] <= "鿿")
    q_en = set(w.lower() for w in re.findall(r"[a-zA-Z]{3,}", q))
    if not q_cn and not q_en:
        return items
    out = []
    for it in items:
        text = it.get("title", "") + " " + it.get("snippet", "")
        t_cn = set(text[i:i+2] for i in range(len(text) - 1) if "一" <= text[i] <= "鿿")
        t_en = set(w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text))
        ov = len(q_cn & t_cn) + len(q_en & t_en)
        if ov >= min_overlap:
            out.append(it)
    return out or items  # 全被剔则保守放行原列表（防误杀清空）


_ZH_STOP = set("的了是在我有和就都不人都一上也说到要去看会着自己这吗呢吧啊呀哦么与或及对为从被把让使跟关于由于因此所以但是然而而且并且不过如果虽然这个那个以及还是还是".split())
_EN_STOP = {"the", "a", "an", "of", "in", "on", "for", "and", "or", "to", "is", "are", "was",
            "were", "be", "been", "being", "with", "by", "at", "from", "as", "that", "this",
            "it", "its", "their", "our", "your", "not", "no", "can", "could", "will", "would",
            "should", "may", "might", "do", "does", "did", "have", "has", "had", "how",
            "what", "when", "where", "who", "which", "why", "about", "into", "over", "after",
            "before", "between", "more", "most", "other", "some", "such", "only", "also",
            "than", "then", "them", "they", "there", "these", "those"}


def _terms(text):
    """jieba 实词提取：去停用词/单字/纯符号，返回小写集合。jieba 不可用时返回 None。"""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import jieba
        jieba.setLogLevel(60)  # 静默 "Building prefix dict" 日志
    except ImportError:
        return None
    out = set()
    for w in jieba.lcut(text):
        w = w.strip().lower()
        if len(w) < 2 or w in _ZH_STOP or w in _EN_STOP:
            continue
        if not re.search(r"[一-鿿a-zA-Z0-9]", w):
            continue
        out.add(w)
    return out


def _hit_count(q_terms, t_terms):
    """子串级匹配计数：词 a 命中 b 当 a⊂b 或 b⊂a（治分词边界差：重庆市↔重庆）。"""
    return sum(1 for a in q_terms if any(a in b or b in a for b in t_terms))


def relevance_grade(items, query, threshold=0.5):
    """相关性分档：查询实词在结果（标题+摘要）中的覆盖率 >= threshold 记 high，否则 low。
    返回 (items, relevant_count, warning)。不改排序只打标；warning 零相关时非空。"""
    q_terms = _terms(query)
    if not q_terms:  # jieba 缺失或查询全是虚词 → 跳过分档
        return items, None, None
    for it in items:
        t = _terms(it.get("title", "") + " " + it.get("snippet", ""))
        cov = _hit_count(q_terms, t) / len(q_terms)
        it["relevance"] = "high" if cov >= threshold else "low"
        it["term_cov"] = round(cov, 2)
    rc = sum(1 for it in items if it["relevance"] == "high")
    warning = (f"0/{len(items)} 条结果含查询实词（{sorted(q_terms)[:10]}）"
               f"——可能搜索词不佳或引擎离题，建议换词/换引擎/--fuse 多引擎") if items and rc == 0 else None
    return items, rc, warning

def rrf_merge(list_of_items, k=60):
    """Reciprocal Rank Fusion：多引擎排名融合（Elasticsearch 同款算法）。
    score = Σ 1/(k+rank)，跨引擎重复结果自然加权靠前，单引擎噪音被稀释。
    同时记录每条结果被哪些引擎命中（engines 列表）——--cross-verify 共识检测的数据基础。"""
    scores, items, engs = {}, {}, {}
    for lst in list_of_items:
        for rank, it in enumerate(lst, 1):
            key = normalize_url(it.get("url", ""))
            if not key:
                continue
            scores[key] = scores.get(key, 0) + 1 / (k + rank)
            items.setdefault(key, it)
            bucket = engs.setdefault(key, [])
            e = it.get("engine")
            if e and e not in bucket:
                bucket.append(e)
    out = []
    for key, sc in sorted(scores.items(), key=lambda x: -x[1]):
        it = dict(items[key])
        it["rrf"] = round(sc, 4)
        it["engines"] = engs.get(key, [])
        out.append(it)
    return out


# ---------- ⑥ 多引擎共识检测（--cross-verify） ----------

_NUM_TOKEN = re.compile(r"(?<![\d.])\d{2,}(?:\.\d+)?(?:%|万|亿|人|元|分|名|所|个|条|次)"
                        r"|(?<![\d.])\d{3,}(?![\d.])")


def _title_sim(a, b):
    """标题相似度：实词集合的包含度（治不同站点同一事件的转载标题差异）。"""
    if not a or not b:
        return 0.0
    ta, tb = _terms(a), _terms(b)
    if not ta or not tb:
        return 1.0 if a.strip() == b.strip() else 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def consensus_clusters(items, threshold=0.6):
    """共识聚类：URL 已由去重归并；再按标题实词包含度 >= threshold 把"同一事件的不同转载"
    并成一组，组内引擎集合 = 该事实的独立来源数。返回 [[item,...], ...]。"""
    clusters = []
    for it in items:
        title = it.get("title", "")
        placed = False
        for cl in clusters:
            if any(_title_sim(title, o.get("title", "")) >= threshold for o in cl):
                cl.append(it)
                placed = True
                break
        if not placed:
            clusters.append([it])
    return clusters


def consensus_annotate(items, threshold=0.6):
    """给每条结果打共识标签：consensus={n, engines, level}。
    level: high(>=3 家引擎)/mid(2 家)/single(仅 1 家孤证)。返回 (items, summary)。"""
    clusters = consensus_clusters(items, threshold)
    for cl in clusters:
        engs = []
        for it in cl:
            for e in it.get("engines") or [it.get("engine")]:
                if e and e not in engs:
                    engs.append(e)
        n = len(engs)
        level = "high" if n >= 3 else ("mid" if n == 2 else "single")
        for it in cl:
            it["consensus"] = {"n": n, "engines": engs, "level": level}
            it["engines"] = engs
    summary = {"clusters": len(clusters),
               "high": sum(1 for cl in clusters if len({e for it in cl for e in (it.get("engines") or [])}) >= 3),
               "mid": sum(1 for cl in clusters if len({e for it in cl for e in (it.get("engines") or [])}) == 2),
               "single": sum(1 for cl in clusters if len({e for it in cl for e in (it.get("engines") or [])}) <= 1)}
    return items, summary


def number_consensus(items, min_engines=2, top=8):
    """跨引擎数字共识：同一数字被 >= min_engines 家引擎的结果提到 → 高可信数据点。
    治"分数线/人数/时间"这类关键数字只出现在单一来源（可能是旧数据/编造）的问题。
    ⚠️ 只做"出现频次"统计，不代表数字正确——不同来源可能集体引用同一错误源。"""
    agg = {}
    for it in items:
        text = it.get("title", "") + " " + it.get("snippet", "")
        engs = [e for e in (it.get("engines") or [it.get("engine")]) if e]
        for tok in set(_NUM_TOKEN.findall(text)):
            d = agg.setdefault(tok, {"value": tok, "engines": set(), "sample": ""})
            d["engines"].update(engs)
            if not d["sample"]:
                d["sample"] = text[:100]
    out = [{"value": v["value"], "n": len(v["engines"]), "engines": sorted(v["engines"]),
            "sample": v["sample"]} for v in agg.values() if len(v["engines"]) >= min_engines]
    out.sort(key=lambda x: (-x["n"], -len(x["value"])))
    return out[:top]


# ---------- ⑫ 广告/软文过滤 ----------

# 三组信号，命中 >=2 组才判疑似（保精确率，防误杀正常内容）
AD_MARKETING = ("立即咨询", "在线咨询", "点击咨询", "免费领取", "限时", "名额有限", "扫码",
                "加微信", "一对一", "报名入口", "免费试听", "预约试听", "专家指导",
                "志愿填报服务", "冲刺班", "冲刺", "复读班", "辅导班", "补习班", "提分技巧",
                "提分", "全托", "集训", "押题", "报名通道", "咨询老师", "招生老师", "速看", "必看")
AD_STRONG = ("包过", "保过", "保录取", "包录取", "内部指标", "提前查分", "改分",
             "包上本科", "签约保", "不过退款", "ensure admission")
# 机构品牌：命中即算"机构特征"信号（与营销话术组合才判，避免误杀提及品牌的新闻）
AD_BRANDS = ("新东方", "学而思", "猿辅导", "作业帮", "高途", "掌门", "精锐", "龙文",
             "卓越教育", "巨人教育", "精华学校", "昂立", "文都", "海文", "启航考研",
             "学大教育", "京翰", "优能", "领航教育", "新概念教育")
AD_CONTACT_RE = re.compile(
    r"(?:微信|QQ|扣扣|电话|热线|手机)\s*[:：]?\s*[\d\-]{5,}"
    r"|(?<!\d)1[3-9]\d{9}(?!\d)"
    r"|(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")
AD_PRICE_RE = re.compile(r"学费|费用|多少钱|收费标准|价格|优惠|折扣|分期|报名费")
AD_BRAND_RE = re.compile(r"[-_|·—]\s*[\u4e00-\u9fa5A-Za-z]{2,10}"
                         r"(?:教育|培训|辅导|网校|学习网|招生网|信息网|考试网|升学网|家教)\s*$")
AD_DOMAIN_HINT = re.compile(r"(peixun|zhaosheng|jiaoyu|kaoshi|xuexi|fudao|shengxue|zhiyuan|baoming)")
# 新闻性表述：稿件在"报道"广告现象（而非本身是广告）——无联系方式时据此免判
AD_NEWS_RE = re.compile(r"记者|报道|调查|回应|表示|称|提醒|警惕|辟谣|曝光|查处|整治|评论|时评")
# 权威站点：命中即豁免（连"价格/联系方式"都不会被当广告证据）
AD_AUTHORITATIVE = ("gov.cn", "edu.cn", ".edu", "chsi.com.cn", "cqksy.cn", "cqzk.com.cn",
                    "moe.gov.cn", "xinhuanet.com", "people.com.cn", "thepaper.cn",
                    "zhihu.com", "wikipedia.org")


def ad_filter(items, config, drop=False):
    """软文/招生广告识别：多信号打分 + 可审计标注（ad_suspect/ad_signals/ad_hits）。

    设计原则（2026-09-27 实测调优）：
    - **只打标不静默删**（默认）：命中项排到最后并附命中理由，人工可复核；--no-ads 才丢弃。
    - **多信号才判**（默认 >=2 组）：单一信号误杀率高（"学费"在高校官网正常、"电话"在
      政务公告正常）。
    - **权威站直接豁免**：gov.cn/edu.cn/chsi.com.cn（阳光高考）/cqksy.cn（重庆考试院）等。
      实测教训：阳光高考那篇《重庆：高考冲刺班1小时400元 老师称或适得其反》同时含
      "冲刺班/一对一/辅导班"与"400元"，纯关键词法必误杀——权威站白名单是必需的一层。
    - **新闻性表述软化**：含 记者/报道/称/提醒 等且无"联系方式"信号时不判（防把报道
      广告现象的稿件当广告）；但"包过/保录取"类违规承诺不受此软化保护。
    - 信号组：营销话术 / 违规承诺（包过类）/ 联系方式 / 价格费用 / 机构品牌特征
      （标题尾 "—XX教育" 或域名含 peixun·zhaosheng·zhiyuan 等拼音）。
    """
    cfg = config.get("ad_filter") or {}
    if cfg.get("enabled") is False:
        return items, {"enabled": False}
    kws = list(AD_MARKETING) + list(cfg.get("keywords") or [])
    strong = list(AD_STRONG) + list(cfg.get("strong_keywords") or [])
    brands = list(AD_BRANDS) + list(cfg.get("brand_keywords") or [])
    white = list(AD_AUTHORITATIVE) + list(cfg.get("whitelist_domains") or [])
    min_signals = int(cfg.get("min_signals", 2))

    flagged, kept = 0, []
    for it in items:
        url = it.get("url", "") or ""
        if any(w in url for w in white):
            kept.append(it)
            continue
        text = (it.get("title", "") or "") + " " + (it.get("snippet", "") or "")
        signals, hits = [], []
        m_hits = [k for k in kws if k in text]
        if m_hits:
            signals.append("营销话术")
            hits.extend(m_hits[:3])
        s_hits = [k for k in strong if k in text]
        if s_hits:
            signals.append("违规承诺")
            hits.extend(s_hits[:2])
        if AD_CONTACT_RE.search(text):
            signals.append("联系方式")
        if AD_PRICE_RE.search(text):
            signals.append("价格费用")
        if AD_BRAND_RE.search(it.get("title", "") or "") or AD_DOMAIN_HINT.search(url):
            signals.append("机构特征")
        b_hits = [b for b in brands if b in text]
        if b_hits:
            signals.append("机构品牌")
            hits.extend(b_hits[:2])
        news_like = bool(AD_NEWS_RE.search(text)) and "联系方式" not in signals
        should_flag = ("违规承诺" in signals) or (len(signals) >= min_signals and not news_like)
        if should_flag:
            it["ad_suspect"] = True
            it["ad_signals"] = signals
            it["ad_hits"] = hits
            flagged += 1
            if drop:
                continue
        kept.append(it)
    # 命中项稳定下沉到末尾（不打乱组内相对顺序）
    kept.sort(key=lambda it: bool(it.get("ad_suspect")))
    stats = {"flagged": flagged, "dropped": bool(drop), "total": len(items)}
    return kept, stats


def domain_concentration(items, min_items=4, ratio=0.7):
    """单一域名占榜检测（**按引擎分组**）：某引擎返回结果里单一域名占比 >=ratio 时给提示。
    实测案例（2026-09-27）：bing 对"重庆高考报名时间"整个首页返回单一 SEO 农场站
    （www.ditu.me 地图迷），"重庆 高考冲刺班 提分"同样整页 farm——relevance_gate 拦不住
    （标题含"重庆"即算相关）。若在合池结果上算集中度会被多引擎稀释掉，必须按引擎分组看。
    只对"仅此一家引擎命中"的条目分组（跨引擎命中的说明不是单一引擎排序问题）。**只提示不删**。"""
    from urllib.parse import urlparse
    by_engine = {}
    for it in items:
        engs = it.get("engines") or ([it.get("engine")] if it.get("engine") else [])
        if len(engs) != 1:
            continue
        by_engine.setdefault(engs[0], []).append(it)
    notes = []
    for eng, lst in by_engine.items():
        if len(lst) < min_items:
            continue
        counts = {}
        for it in lst:
            try:
                dom = urlparse(it.get("url", "")).netloc
            except Exception:
                continue
            counts[dom] = counts.get(dom, 0) + 1
        if not counts:
            continue
        dom, n = max(counts.items(), key=lambda x: x[1])
        if n / len(lst) >= ratio:
            notes.append(f"{eng} 结果被单一域名占榜：{dom} 占 {n}/{len(lst)} 条——疑似该引擎排序"
                         f"被 SEO 农场污染，本次建议不看该引擎结果，或换查询说法/换引擎复核")
    return notes


def fetch_all_fused(query, order, count, timeout, config, fuse_n, days=None):
    """并发取前 fuse_n 个可用引擎，RRF 融合（互相纠偏，单引擎垃圾被稀释）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    usable = [n for n in order if n in engines.ENGINES][:fuse_n]
    lists, used, skipped = [], [], []
    with ThreadPoolExecutor(max_workers=len(usable) or 1) as ex:
        futs = {ex.submit(_call_engine, n, query, count, timeout, config, days): n
                for n in usable}
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                items = fut.result()
                if items:
                    used.append(n)
                    for it in items:
                        it["engine"] = n
                    lists.append(items)
                else:
                    skipped.append(f"{n}: 零结果")
            except KeyError as e:
                skipped.append(f"{n}: 缺 key（{e}）")
            except Exception as e:
                skipped.append(f"{n}: {type(e).__name__} {str(e)[:80]}")
    merged = rrf_merge(lists)
    return merged, used, skipped


def _call_engine(name, query, count, timeout, config, days=None):
    """统一引擎调用入口：--days 只传给原生支持的引擎（名单外强传会 TypeError）。"""
    fn = engines.ENGINES[name]
    if days and name in engines.TIME_CAPABLE:
        return fn(query, count=count, timeout=timeout, config=config, days=days)
    return fn(query, count=count, timeout=timeout, config=config)


def fetch_all(query, order, count, timeout, config, days=None):
    merged, used, skipped = [], [], []
    for name in order:
        fn = engines.ENGINES.get(name)
        if not fn:
            skipped.append(f"{name}: 未知引擎")
            continue
        try:
            items = _call_engine(name, query, count, timeout, config, days)
            if not items:
                skipped.append(f"{name}: 零结果或解析失败")
                continue
            used.append(name)
            for it in items:
                it["engine"] = name
                merged.append(it)
        except KeyError as e:
            skipped.append(f"{name}: 缺 key（{e}）")
        except Exception as e:
            skipped.append(f"{name}: {type(e).__name__} {str(e)[:100]}")
    return merged, used, skipped


def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = normalize_url(it.get("url", ""))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(it)
    return out


def rescore(items, pref_domains):
    if not pref_domains:
        return items
    return sorted(items, key=lambda it: not any(d in it.get("url", "") for d in pref_domains))


def main():
    ap = argparse.ArgumentParser(description="多引擎免key/API 联网搜索")
    ap.add_argument("query")
    ap.add_argument("--engine", default="auto", help="逗号分隔引擎名或 auto")
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--rerank", action="store_true", help="按查询相关度重排（DMXAPI /v1/rerank 真端点，失败自动回退余弦）")
    ap.add_argument("--read", type=int, default=0, metavar="N", help="深读前N条结果正文（默认0=只给摘要）")
    ap.add_argument("--fuse", type=int, default=0, metavar="N", help="并发前N个可用引擎做RRF融合排序（推荐2-3，默认0=首个成功即停）")
    ap.add_argument("--cross-verify", nargs="?", const=0, type=int, default=None, metavar="N",
                    help="多引擎共识检测：并发 N 个引擎（N 省略时取 config.cross_verify.engines，默认3），"
                         "标注每条结果是「几家引擎共同命中」（consensus.level: high/mid/single）+ 跨引擎数字共识。"
                         "注意：默认池前3个含付费引擎，省钱请配 --engine bing_html,baidu_html,zhipu")
    ap.add_argument("--no-ads", action="store_true", help="丢弃疑似招生广告/软文（默认只打标下沉，不删）")
    ap.add_argument("--days", type=int, default=None, metavar="N",
                    help="只看最近 N 天：zhipu/tavily 走引擎原生时间过滤，不支持的原生过滤的引擎如实标注"
                         "（HTML 引擎没有该能力，不伪装——别拿 --days 当硬保证）")
    ap.add_argument("--no-cache", action="store_true", help="跳过查询缓存（默认开，TTL 6h；时效敏感查询用）")
    ap.add_argument("--embed", action="store_true", help="输出附加向量（默认关闭）")
    ap.add_argument("--strict", action="store_true", help="只保留相关性 high 的结果（默认全保留仅打标）")
    args = ap.parse_args()
    config = load_config()
    count = args.count or config.get("default_count", 10)
    timeout = config.get("timeout_seconds", 15)

    if args.engine and args.engine != "auto":
        order = [e.strip() for e in args.engine.split(",") if e.strip()]
    else:
        order = config.get("engine_priority") or ["bing_html", "ddg_html"]

    cv_cfg = config.get("cross_verify") or {}
    cv_n = None
    if args.cross_verify is not None:
        cv_n = args.cross_verify or int(cv_cfg.get("engines", 3))
        cv_n = max(cv_n, 2)  # 共识检测至少要比 2 家
    fuse_n = max(args.fuse or 0, cv_n or 0)

    # ---- 查询缓存（整结果级；命中直接回，省 paid 按次费 + 免重跑 --read/--rerank）----
    from cache import key as ckey, get as cget, put as cput
    cfg_fp = ckey(json.dumps({k: v for k, v in config.items() if k != "api_keys"},
                             sort_keys=True, ensure_ascii=False))
    ck = ckey("search", args.query, ",".join(order), count, fuse_n, cv_n,
              args.days, args.no_ads, args.rerank, args.embed, args.strict, args.read, cfg_fp)
    if not args.no_cache:
        hit = cget(ck, config)
        if hit:
            payload, age_min = hit
            payload["cache"] = {"hit": True, "age_minutes": age_min}
            print(f"缓存命中（{age_min} 分钟前结果；--no-cache 强制重搜）", file=sys.stderr)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

    time_filter = None
    if args.days:
        attempted = [n for n in order if n in engines.ENGINES]
        applied = [n for n in attempted if n in engines.TIME_CAPABLE]
        unsupported = [n for n in attempted if n not in engines.TIME_CAPABLE]
        time_filter = {"days": args.days, "applied": applied, "not_supported": unsupported}
        if not applied:
            print(f"⚠️ --days 被忽略：本轮引擎均不支持原生时间过滤（{attempted}）"
                  f"——建议 --engine zhipu 或 tavily", file=sys.stderr)

    if fuse_n:
        merged, used, skipped = fetch_all_fused(args.query, order, count, timeout, config, fuse_n, days=args.days)
    else:
        merged, used, skipped = fetch_all(args.query, order, count, timeout, config, days=args.days)
    items = dedupe(merged)
    blocked = config.get("block_domains") or []
    if blocked:
        items = [it for it in items if not any(b in it.get("url", "") for b in blocked)]
    items = relevance_gate(items, args.query)
    items, rel_count, rel_warn = relevance_grade(items, args.query)
    if args.strict and rel_count is not None:
        items = [it for it in items if it.get("relevance") == "high"]

    cv_summary, num_consensus = None, None
    if cv_n is not None:
        items, cv_summary = consensus_annotate(items, float(cv_cfg.get("title_overlap", 0.6)))
        num_consensus = number_consensus(items, int(cv_cfg.get("min_number_engines", 2)))
        cv_summary["number_consensus"] = num_consensus

    items, ad_stats = ad_filter(items, config, drop=args.no_ads)
    items = rescore(items, config.get("rescore_domains") or [])
    quality = domain_concentration(items)

    if args.read:
        try:
            from read_page import read
            for it in items[:args.read]:
                try:
                    t, c, m = read(it["url"], config=config)
                    it["page_content"] = c[:4000]
                    it["read_method"] = m
                except Exception as e:
                    it["read_error"] = str(e)[:80]
        except Exception as e:
            skipped.append(f"read: {type(e).__name__} {str(e)[:80]}")

    if args.rerank or args.embed:
        try:
            from embed_results import annotate
            items, notes = annotate(items, query=args.query, config=config,
                                    do_rerank=args.rerank, do_embed=args.embed)
            skipped.extend(notes)
        except Exception as e:
            skipped.append(f"embed/rerank: {type(e).__name__} {str(e)[:100]}")

    result = {"query": args.query, "engine_used": used,
              "total": len(items), "relevant_count": rel_count,
              "warning": rel_warn,
              "items": items, "skipped": skipped}
    if cv_summary is not None:
        result["cross_verify"] = cv_summary
    result["ad_filter"] = ad_stats
    if time_filter:
        result["time_filter"] = time_filter
    if quality:
        result["quality_notes"] = quality
    from datetime import datetime
    result["cache"] = {"hit": False, "stored_at": datetime.now().isoformat(timespec="seconds")}
    if not args.no_cache:
        cput(ck, result, config)
    if rel_warn:
        print(f"⚠️ {rel_warn}", file=sys.stderr)
    for note in quality:
        print(f"⚠️ {note}", file=sys.stderr)
    if cv_summary:
        print(f"共识检测：high={cv_summary['high']} mid={cv_summary['mid']} "
              f"single(孤证)={cv_summary['single']} / 共 {cv_summary['clusters']} 组",
              file=sys.stderr)
    if ad_stats.get("flagged"):
        print(f"⚠️ 疑似招生广告/软文 {ad_stats['flagged']} 条"
              f"{'（已丢弃）' if ad_stats['dropped'] else '（已打标并下沉，复核 ad_signals）'}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
