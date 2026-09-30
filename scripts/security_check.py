#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布/分享前密钥与隐私自查（开源准备⑩的落地工具）。

用法:
  python scripts/security_check.py            # 扫描 skill 根目录（自动定位），JSON 输出
  python scripts/security_check.py <目录>     # 扫描指定目录
退出码: 0=干净可发布；1=发现疑似密钥或 .gitignore 缺失。
依赖: 仅标准库。
扫描内容:
  ①常见密钥模式（sk- 前缀 / Bearer 长串 / api_key|token 赋值长串 / 32+ hex）
  ②敏感文件存在性（secrets.json / cache.db / *.db）——存在即提示（它们应被 .gitignore 覆盖且不进发布包）
  ③.gitignore 是否覆盖 secrets.json 与 cache.db
注意: 模式法只能抓"长得像密钥"的串，不能替代人工Review——发布前仍需人工过一遍 diff。
"""
import json
import re
import sys
from pathlib import Path

PATTERNS = [
    ("sk 前缀 key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("Bearer 长串", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{24,}")),
    ("api_key/token 赋值长串", re.compile(r"""(?:api_key|apiKey|token|secret|password)\s*[:=]\s*["'][A-Za-z0-9_\-]{16,}["']""", re.I)),
    ("32+ 位 hex（疑似 key/签名）", re.compile(r"\b[a-f0-9]{32,}\b", re.I)),
]
TEXT_EXT = {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".js"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "out"}
SENSITIVE_FILES = {"secrets.json", "cache.db"}


def scan(root: Path):
    findings, sensitive_present = [], []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name in SENSITIVE_FILES or p.suffix == ".db":
            sensitive_present.append(rel)
            continue
        if p.suffix not in TEXT_EXT:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for label, pat in PATTERNS:
            for m in pat.finditer(text):
                s = m.group(0)
                # 长串打码，避免自查报告自己变成泄密源
                findings.append({"file": rel, "type": label,
                                 "sample": s[:6] + "…" + s[-4:] if len(s) > 12 else s,
                                 "line": text[:m.start()].count("\n") + 1})
    gi = root / ".gitignore"
    gi_ok = gi.exists()
    gi_text = gi.read_text(encoding="utf-8", errors="ignore") if gi_ok else ""
    gi_missing = [f for f in ("secrets.json", "cache.db") if gi_ok and f not in gi_text]
    return {"root": str(root), "findings": findings,
            "sensitive_files_present": sensitive_present,
            "gitignore_exists": gi_ok, "gitignore_missing": gi_missing,
            "clean": not findings and gi_ok and not gi_missing}


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
        else Path(__file__).resolve().parent.parent
    rep = scan(root)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0 if rep["clean"] else 1)


if __name__ == "__main__":
    main()
