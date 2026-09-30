#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图搜图 · 本地版：感知哈希（pHash）在本地图片库找相似图。

用法:
  python similar_image_local.py 目标图片.jpg 图片目录 [--limit 10]
输出: JSON [{file, distance(=汉明距离，越小越像), score}]
依赖: Pillow（已装）
适用: 找同一张题的翻拍/重复图、查重；与联网以图搜图互补。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image


def phash(img, size=32, hash_size=8):
    img = img.convert("L").resize((size, size), Image.LANCZOS)
    px = list(img.getdata())
    avg = sum(px) / len(px)
    bits = [1 if v > avg else 0 for v in px]
    return bits


def dhash(img, hash_size=8):
    """差异哈希：比较相邻像素亮度差，对线条/文字结构更敏感。"""
    img = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    px = list(img.getdata())
    bits = []
    for r in range(hash_size):
        for c in range(hash_size):
            bits.append(1 if px[r * (hash_size + 1) + c] > px[r * (hash_size + 1) + c + 1] else 0)
    return bits


ALGOS = {"phash": phash, "dhash": dhash}


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def main():
    ap = argparse.ArgumentParser(description="图搜图（本地相似检索）")
    ap.add_argument("target")
    ap.add_argument("image_dir")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--algo", default="phash", choices=["phash", "dhash"],
                    help="phash=照片/风景类更稳；dhash=线条/文字/题图更稳")
    args = ap.parse_args()

    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    hasher = ALGOS[args.algo]
    try:
        target_h = hasher(Image.open(args.target))
    except Exception as e:
        print(json.dumps({"error": f"目标图无法读取：{e}"}, ensure_ascii=False))
        sys.exit(1)

    results = []
    for p in Path(args.image_dir).rglob("*"):
        if p.suffix.lower() not in exts:
            continue
        try:
            h = hasher(Image.open(p))
        except Exception:
            continue
        d = hamming(target_h, h)
        results.append({"file": str(p), "distance": d, "score": round(1 - d / 256, 3)})
    results.sort(key=lambda r: r["distance"])
    print(json.dumps(results[:args.limit], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
