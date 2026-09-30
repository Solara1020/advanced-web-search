#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deep_search — MindSearch 式双层多智能体深度研究（完整版）。

架构：
  外层 Planner（大杯模型，config.deep_search.planner_model）：问题 → 子查询 DAG（JSON）
  内层 Searcher（每节点）：引擎池搜索 → read top2 → 小杯模型整合该节点结论
  缺口轮：Planner 看节点结论后可追加补漏节点（--rounds 控制轮数）
  Writer（小杯）：全节点结论 → 带引用综合报告
预算：max_llm_calls / max_engine_calls / deadline 三重硬约束，超限强制收敛出报告。

用法:
  python deep_search.py "问题" [--out 报告.md] [--rounds 2] [--max-nodes 6] [--timeout 480]
输出: markdown 报告（--out 落盘 + stdout 输出全文）
依赖: engines.py / read_page.py / common.py（DMXAPI key）
"""
import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

from common import dmxapi_key, load_config
import engines
from read_page import read


# ---------- 预算 ----------

class Budget:
    def __init__(self, max_llm, max_engine, deadline_s):
        self.max_llm, self.max_engine = max_llm, max_engine
        self.llm = self.engine = 0
        self.deadline = time.time() + deadline_s
        self.lock = threading.Lock()

    def tick_llm(self):
        with self.lock:
            self.llm += 1
            return self.llm <= self.max_llm

    def tick_engine(self):
        with self.lock:
            self.engine += 1
            return self.engine <= self.max_engine

    def expired(self):
        return time.time() > self.deadline


# ---------- LLM ----------

def llm_chat(model, messages, config, budget, max_tokens=2000):
    if not budget.tick_llm():
        raise RuntimeError(f"LLM 预算耗尽（max {budget.max_llm}）")
    key = dmxapi_key(config)
    base = (config or {}).get("dmxapi", {}).get("base_url", "https://www.dmxapi.cn/v1")
    # 默认关闭思考：deepseek 系思考会吃光 max_tokens 导致正文为空（实测坑）
    # 个别模型（GLM 系）不接受该参数 → 400 时自动去掉重试
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "thinking": {"type": "disabled"}}
    r = requests.post(f"{base}/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {key}"}, timeout=180)
    if r.status_code == 400 and "thinking" in r.text:
        body.pop("thinking")
        r = requests.post(f"{base}/chat/completions", json=body,
                          headers={"Authorization": f"Bearer {key}"}, timeout=180)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


# ---------- Planner ----------

PLANNER_PROMPT = """你是深度研究规划器。把用户问题拆解为可联网检索的子问题 DAG。
只输出 JSON（无任何其它文本），格式：
{"nodes":[{"id":"n1","query":"可直接喂搜索引擎的具体搜索词","goal":"该节点要查明的事实","depends_on":[]}]}
规则：2-{max_nodes} 个节点；query 用 3-8 个关键词组成（含年份/地域限定），禁止整句自然语言；有依赖的节点把前置节点 id 写入 depends_on；无依赖节点可并发。"""


def plan(question, config, budget, max_nodes):
    model = (config.get("deep_search") or {}).get("planner_model", "qwen3.7-plus")
    # 注意：模板含 JSON 花括号，禁止用 .format()（会把 JSON 解析成格式字段，实测踩坑）
    sys_prompt = PLANNER_PROMPT.replace("{max_nodes}", str(max_nodes))
    for attempt in range(2):
        try:
            out = llm_chat(model, [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": question}],
                config, budget, max_tokens=1500)
            m = re.search(r"\{.*\}", out, re.S)
            nodes = json.loads(m.group(0)).get("nodes") or []
            nodes = [n for n in nodes if isinstance(n, dict) and n.get("id") and n.get("query")][:max_nodes]
            if nodes:
                return nodes
        except Exception:
            if attempt == 1:
                break
            time.sleep(1)
    # Planner 失败 → 降级单节点
    return [{"id": "n1", "query": question, "goal": question, "depends_on": []}]


# ---------- 内层 Searcher ----------

def run_node(node, config, budget, k_results=8, read_top=3):
    """单节点：搜索（fuse 融合+守门分档） → 读 top 页 → LLM 整合节点结论。"""
    from search import fetch_all_fused, relevance_gate, relevance_grade  # 延迟导入避免环
    ds = config.get("deep_search") or {}
    searcher_model = ds.get("writer_model", "deepseek-v4-flash")
    dep_ctx = ""
    if node.get("_dep_results"):
        dep_ctx = "\n\n前置节点已知结论：\n" + "\n".join(
            f"- [{nid}] {txt[:300]}" for nid, txt in node["_dep_results"])

    # 1) 搜索：并发前 fuse_n 引擎 RRF 融合 + 守门分档
    #    （修复 P0 遗留：原裸 fetch 取首个成功引擎，单引擎离题垃圾直接污染节点结论）
    items, used_engine, rel_count = [], "", None
    fuse_n = max(1, int(ds.get("fuse", 2)))
    order = [n for n in (config.get("engine_priority") or ["bing_html"]) if n in engines.ENGINES]
    n_try = 0
    while n_try < min(fuse_n, len(order)) and budget.tick_engine():
        n_try += 1
    if n_try:
        try:
            items, used_list, _skipped = fetch_all_fused(
                node["query"], order, k_results, 15, config, n_try)
            used_engine = "+".join(used_list)
        except Exception:
            items = []
    items = relevance_gate(items, node["query"])
    items, rel_count, rel_warn = relevance_grade(items, node["query"])
    items.sort(key=lambda it: it.get("relevance") == "high", reverse=True)  # high 优先深读

    # 2) 深读 top 页
    pages = []
    for it in items[:read_top]:
        if budget.expired():
            break
        try:
            title, content, method = read(it["url"], config=config)
            if len(content) > 150:
                pages.append({"url": it["url"], "title": title, "content": content[:2500]})
        except Exception:
            continue

    # 3) 节点结论整合
    src_lines = "\n".join(f"[{i+1}] {p['title']} ({p['url']})\n{p['content'][:1200]}"
                          for i, p in enumerate(pages))
    if not src_lines:
        src_lines = "\n".join(f"[{i+1}] {it['title']} ({it['url']})\n{it.get('snippet','')}"
                              for i, it in enumerate(items))
    goal = node.get("goal", node["query"])
    low_rel_note = ("\n\n⚠️ 本次搜索结果与查询词相关性普遍偏低：仅可引用确实与目标相关的内容，"
                    "材料不足就明说，不得强行归纳。") if rel_count == 0 else ""
    try:
        conclusion = llm_chat(searcher_model, [
            {"role": "system",
             "content": "你是研究助理。依据给定搜索材料回答节点目标；只陈述材料支持的事实，"
                        "每条事实后标注来源编号如[1]；材料不足时明说。300字内。"},
            {"role": "user", "content": f"节点目标：{goal}{dep_ctx}{low_rel_note}\n\n搜索材料：\n{src_lines}"}],
            config, budget, max_tokens=800)
    except Exception as e:
        conclusion = f"（节点结论生成失败：{str(e)[:80]}；可用摘要：{items[0].get('snippet','')[:200] if items else '无'}）"

    return {"id": node["id"], "query": node["query"], "goal": goal,
            "engine": used_engine, "relevant_count": rel_count,
            "sources": [p["url"] for p in pages] or [it["url"] for it in items[:3]],
            "conclusion": conclusion}


# ---------- 拓扑执行 ----------

def execute_dag(nodes, config, budget, pool=3):
    done = {}
    pending = {n["id"]: dict(n) for n in nodes}
    errors = []
    while pending and not budget.expired():
        ready = [n for n in pending.values()
                 if all(d in done for d in (n.get("depends_on") or []))]
        if not ready:
            errors.append(f"依赖死锁：{list(pending)}")
            break
        with ThreadPoolExecutor(max_workers=pool) as ex:
            futs = {ex.submit(run_node, n, config, budget): n["id"] for n in ready}
            for fut in as_completed(futs):
                nid = futs[fut]
                try:
                    res = fut.result()
                    done[nid] = res
                except Exception as e:
                    errors.append(f"节点 {nid}: {type(e).__name__} {str(e)[:80]}")
                pending.pop(nid, None)
    return done, errors


# ---------- 缺口轮与报告 ----------

def gap_nodes(question, done, config, budget, max_nodes):
    """Planner 检查已得结论，追加 ≤2 个补漏节点。
    硬规则：任何节点结论含失败关键词时必须补搜（不信 LLM 自由判断）。"""
    model = (config.get("deep_search") or {}).get("planner_model", "qwen3.7-plus")
    known = "\n".join(f"- [{r['id']}] {r['goal']}：{r['conclusion'][:200]}" for r in done.values())
    fail_hit = any(re.search(r"无法|未找到|材料不足|无关|失败", r["conclusion"]) for r in done.values())
    force = "已有结论中存在'无法回答/材料无关'的节点，你必须生成补充检索节点。" if fail_hit else ""
    try:
        out = llm_chat(model, [
            {"role": "system",
             "content": "检查研究结论是否回答了用户问题。若有关键缺口，输出补充检索节点的 JSON："
                        "{\"nodes\":[{\"id\":\"g1\",\"query\":\"...\",\"goal\":\"...\",\"depends_on\":[]}]}（≤2个，"
                        "query 必须是具体的短搜索词组，不要整句自然语言）；"
                        "若无缺口，输出 {\"nodes\":[]}。只输出 JSON。" + force},
            {"role": "user", "content": f"用户问题：{question}\n\n已有结论：\n{known}"}],
            config, budget, max_tokens=800)
        m = re.search(r"\{.*\}", out, re.S)
        nodes = json.loads(m.group(0)).get("nodes") or []
        return [n for n in nodes if isinstance(n, dict) and n.get("query")][:2]
    except Exception:
        # LLM 缺口判断失败但检测到失败关键词 → 用原问题核心词兜底补搜一次
        if fail_hit:
            return [{"id": "g1", "query": question[:30], "goal": question}]
        return []


def write_report(question, done, config, budget):
    model = (config.get("deep_search") or {}).get("writer_model", "deepseek-v4-flash")
    body = "\n\n".join(f"### [{r['id']}] {r['goal']}\n{r['conclusion']}\n来源：{', '.join(r['sources'][:3])}"
                       for r in done.values())
    all_src = sorted({u for r in done.values() for u in r["sources"]})
    src_list = "\n".join(f"{i+1}. {u}" for i, u in enumerate(all_src))
    try:
        summary = llm_chat(model, [
            {"role": "system",
             "content": "综合以下分节点研究结论，写一份带编号引用的中文综述（400-700字），"
                        "事实必须来自结论材料，结尾给一句'信息缺口'说明。"},
            {"role": "user", "content": f"问题：{question}\n\n分节点结论：\n{body}"}],
            config, budget, max_tokens=1500)
    except Exception as e:
        summary = f"（综述生成失败：{str(e)[:80]}，以下为分节点原始结论）\n\n{body}"

    report = (f"# 深度研究：{question}\n\n"
              f"> {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
              f"LLM {budget.llm}/{budget.max_llm} 次 · 引擎 {budget.engine}/{budget.max_engine} 次\n\n"
              f"## 综述\n\n{summary}\n\n## 分节点结论\n\n{body}\n\n## 全部来源\n\n{src_list}\n")
    return report


# ---------- 主流程 ----------

def deep_search(question, config=None, rounds=2, max_nodes=6, timeout_s=480, verbose=True):
    config = config or load_config()
    ds = (config.get("deep_search") or {})
    budget = Budget(ds.get("max_llm_calls", 4) + rounds * 3, ds.get("max_engine_calls", 12), timeout_s)

    def log(msg):
        if verbose:
            print(f"[deep_search] {msg}", file=sys.stderr)

    nodes = plan(question, config, budget, max_nodes)
    log(f"Planner 拆解 {len(nodes)} 节点：{[n['query'][:24] for n in nodes]}")

    done, errors = execute_dag(nodes, config, budget)

    for r_i in range(rounds - 1):
        if budget.expired():
            break
        extra = gap_nodes(question, done, config, budget, max_nodes)
        if not extra:
            log(f"缺口轮 {r_i+1}：无缺口，收敛")
            break
        log(f"缺口轮 {r_i+1}：追加 {[n['query'][:24] for n in extra]}")
        more, errs = execute_dag(extra, config, budget)
        done.update(more)
        errors += errs

    report = write_report(question, done, config, budget)
    return report, {"nodes": len(done), "errors": errors,
                    "llm": budget.llm, "engine": budget.engine}


def main():
    ap = argparse.ArgumentParser(description="deep_search 双层多智能体深度研究")
    ap.add_argument("question")
    ap.add_argument("--out", help="报告落盘路径（.md）")
    ap.add_argument("--rounds", type=int, default=None)
    ap.add_argument("--max-nodes", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=None, help="总时限秒")
    args = ap.parse_args()
    config = load_config()
    ds = config.get("deep_search") or {}
    report, meta = deep_search(
        args.question, config=config,
        rounds=args.rounds or ds.get("rounds", 2),
        max_nodes=args.max_nodes or 6,
        timeout_s=args.timeout or 480)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n[已落盘] {args.out}", file=sys.stderr)
    print(f"\n[meta] {json.dumps(meta, ensure_ascii=False)}", file=sys.stderr)


if __name__ == "__main__":
    main()
