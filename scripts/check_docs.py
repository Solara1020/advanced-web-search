#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档防漂移校验：核对 SKILL.md 的 config 样例 与 实际 config.json 的关键字段是否一致。

用法:
  python scripts/check_docs.py          # 在 skill 根目录跑；一致 exit 0，不一致 exit 1
依赖: 仅标准库。
背景: 2026-09 实锤过一次漂移（deep_search 模型名 config 改了、SKILL.md 样例漂了 16 天没人发现）。
      本脚本把"关键字段"作为白名单逐一比对；样例带 // 注释，解析前先按字符串感知方式剥注释。
范围: 只比白名单字段（不全文比对，避免注释/排版差异误报）。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 关键字段白名单（config 与文档样例必须一致）
KEYS = [
    "engine_priority", "default_count", "image_engines",
    "cross_verify.engines", "cross_verify.title_overlap",
    "ad_filter.enabled", "ad_filter.min_signals",
    "cache.enabled", "cache.ttl_hours",
    "deep_search.planner_model", "deep_search.writer_model",
    "zhipu.search_engine",
    "log_runs.enabled", "dedupe.enabled", "dedupe.title_threshold",
    "rescue.enabled", "deadline_seconds",
]


def strip_jsonc(text):
    """剥掉 JSON 里的 // 行注释（字符串感知：URL 里的 // 不受影响）。"""
    out, i, n, in_str, esc = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def dig(d, path):
    cur = d
    for k in path.split("."):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return "<缺失>"
    return cur


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"```json\n(.*?)```", md, re.S)
    if not m:
        print("✗ SKILL.md 未找到 config 样例代码块（```json ... ```）")
        sys.exit(1)
    try:
        sample = json.loads(strip_jsonc(m.group(1)))
    except Exception as e:
        print(f"✗ SKILL.md config 样例解析失败：{e}")
        sys.exit(1)

    bad = []
    for key in KEYS:
        a, b = dig(sample, key), dig(cfg, key)
        if a != b:
            bad.append(f"{key}: 文档样例={a!r}  实际 config={b!r}")
    if bad:
        print(f"✗ 文档与 config 漂移 {len(bad)} 处：")
        for line in bad:
            print("   -", line)
        print("（修 SKILL.md 样例使其与 config.json 一致，或反向修正 config）")
        sys.exit(1)
    print(f"✓ 文档无漂移：{len(KEYS)} 个关键字段与 config.json 一致")


if __name__ == "__main__":
    main()
