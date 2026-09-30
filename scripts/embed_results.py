#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结果后处理：DMXAPI 向量化（embedding）+ 重排（rerank）。

功能（默认关闭，search.py --embed / --rerank 或 config 打开）：
  - do_rerank：优先走 DMXAPI /v1/rerank 真端点（config.rerank.model，默认 qwen3-rerank）；
    端点失败自动回退查询-结果余弦相似度重排（config.embed.model）
  - do_embed：给每条结果附加 embedding（模型见 config.embed.model）
  - --dedup（独立模式）：相似度 > 0.92 的结果对判定为重复，输出建议合并列表

独立用法:
  python embed_results.py --file results.json --dedup
  python embed_results.py --file results.json --rerank "查询词"
  echo '[{"title":..,"url":..,"snippet":..}]' | python embed_results.py --rerank "查询词"

依赖: requests（DMXAPI OpenAI 兼容 /v1/embeddings + Jina/Cohere 兼容 /v1/rerank）。key 见 common.dmxapi_key()。
"""
import argparse
import json
import sys

import requests

from common import dmxapi_key, load_config

DEFAULTS = {"model": "text-embedding-3-small", "batch": 50, "topk": 20}
SIM_THRESHOLD = 0.92


def _texts(items):
    return [(it.get("title", "") + " " + it.get("snippet", ""))[:1500] for it in items]


def embed_texts(texts, config=None):
    key = dmxapi_key(config)
    if not key:
        raise RuntimeError("DMXAPI key 不可用（env DMXAPI_KEY 或 .zcode/v2/config.json）")
    base = (config or {}).get("dmxapi", {}).get("base_url", "https://www.dmxapi.cn/v1")
    model = (config or {}).get("embed", {}).get("model", DEFAULTS["model"])
    batch = (config or {}).get("embed", {}).get("batch", DEFAULTS["batch"])
    out = []
    for i in range(0, len(texts), batch):
        r = requests.post(f"{base}/embeddings",
                          json={"model": model, "input": texts[i:i + batch]},
                          headers={"Authorization": f"Bearer {key}"}, timeout=60)
        r.raise_for_status()
        data = r.json()
        for item in data.get("data", []):
            out.append(item.get("embedding", []))
    return out


def cos(a, b):
    la = sum(x * x for x in a) ** 0.5
    lb = sum(x * x for x in b) ** 0.5
    if not la or not lb:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (la * lb)


def rerank_texts(query, texts, config=None):
    """DMXAPI /v1/rerank 真端点（Jina/Cohere 兼容格式，模型默认 qwen3-rerank）。
    返回 [(原索引, relevance_score)]，按分数降序。失败抛异常，调用方决定回退。"""
    key = dmxapi_key(config)
    if not key:
        raise RuntimeError("DMXAPI key 不可用")
    base = (config or {}).get("dmxapi", {}).get("base_url", "https://www.dmxapi.cn/v1")
    model = (config or {}).get("rerank", {}).get("model", "qwen3-rerank")
    r = requests.post(f"{base}/rerank",
                      json={"model": model, "query": query, "documents": texts,
                            "top_n": len(texts)},
                      headers={"Authorization": f"Bearer {key}"}, timeout=60)
    r.raise_for_status()
    results = r.json().get("results", [])
    return [(int(it["index"]), float(it.get("relevance_score", 0))) for it in results]


def _rerank_by_cos(items, query, config, notes):
    """余弦回退路径：embedding 查询与结果算余弦重排。"""
    vecs = embed_texts(_texts(items), config)
    qv = embed_texts([query], config)[0]
    for it, v in zip(items, vecs):
        it["_cos"] = round(cos(qv, v), 4)
    items.sort(key=lambda it: it.get("_cos", 0), reverse=True)
    notes.append("rerank: 余弦回退（config.embed.model）")


def annotate(items, query=None, config=None, do_rerank=False, do_embed=False):
    """给 items 附加/重排；返回 (新items, 处理说明列表)。失败抛异常由调用方兜底。"""
    if not items:
        return items, []
    notes = []
    if do_rerank:
        if not query:
            raise RuntimeError("--rerank 需要指定查询词（未提供时跳过重排）")
        topk = (config or {}).get("rerank", {}).get("topk", 20)
        head, tail = items[:topk], items[topk:]  # 只重排前 topk 条，其余原序垫后
        try:
            order = rerank_texts(query, _texts(head), config)
            head = [head[i] for i, sc in order]
            for it, (_, sc) in zip(head, order):
                it["_rerank_score"] = round(sc, 4)
            items = head + tail
            notes.append(f"rerank: 真端点（config.rerank.model）")
        except Exception as e:
            notes.append(f"rerank: 真端点失败（{type(e).__name__} {str(e)[:60]}）→ 余弦回退")
            _rerank_by_cos(head, query, config, notes)
            items = head + tail
    if do_embed:
        vecs = embed_texts(_texts(items), config)
        for it, v in zip(items, vecs):
            it["embedding"] = v[:8]  # 只留前8维展示，全文不塞回上下文；消费方可再取
    return items, notes


def dedup_report(items, config=None):
    texts = _texts(items)
    vecs = embed_texts(texts, config)
    pairs = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            s = cos(vecs[i], vecs[j])
            if s >= SIM_THRESHOLD:
                pairs.append({"i": i, "j": j, "cos": round(s, 3),
                              "url_i": items[i].get("url", ""), "url_j": items[j].get("url", "")})
    return pairs


def main():
    ap = argparse.ArgumentParser(description="结果向量化/语义去重（DMXAPI，默认关闭的功能）")
    ap.add_argument("--file", help="结果 JSON 文件（列表或 {items:[..]}）")
    ap.add_argument("--rerank", metavar="QUERY", help="按查询重排")
    ap.add_argument("--dedup", action="store_true", help="相似度>0.92 的重复对报告")
    args = ap.parse_args()
    config = load_config()
    if args.file:
        data = json.loads(open(args.file, encoding="utf-8").read())
        items = data.get("items") if isinstance(data, dict) else data
    else:
        items = json.loads(sys.stdin.read())
    try:
        if args.dedup:
            pairs = dedup_report(items, config)
            print(json.dumps({"similar_pairs": pairs, "threshold": SIM_THRESHOLD},
                             ensure_ascii=False, indent=2))
        elif args.rerank:
            new_items, notes = annotate(items, query=args.rerank, config=config,
                                        do_rerank=True, do_embed=True)
            print(json.dumps({"notes": notes, "items": new_items},
                             ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__} {str(e)[:150]}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
