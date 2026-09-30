#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图搜文（OCR）：三通道——视觉大模型（保真最高，DMXAPI）/ easyocr（本地免费）/ tesseract。

用法:
  python ocr_image.py 图片路径 [--via vl|easyocr|tesseract] [--detail] [--prompt 自定义指令]
  python ocr_image.py 图片路径 --via vl --model Doubao-1.5-vision-pro-32k
输出: 默认纯文本到 stdout；--detail 时 JSON {via, text, ...}
依赖: requests（vl 通道，key 见 common.dmxapi_key）；easyocr（本地，首跑下载模型慢）；pytesseract
说明: vl 通道自动压缩大图（长边 >1568px 压缩，防中转丢图——2026-08 实测 4MB/3000px 图被丢）；
      手写体/公式/复杂排版首选 vl；纯印刷体批量用 easyocr（零成本）。
"""
import argparse
import base64
import io
import json
import sys

from common import dmxapi_key, load_config

VL_DEFAULT_MODEL = "Doubao-1.5-vision-pro-32k"  # 2026-08 实测转录保真第一
VL_PROMPT = ("逐字原样转录图中所有可见文字，一行对一行地保留。"
             "严禁改写、严禁补充图中没有的内容、严禁把行首标签推断成标题或结论"
             "（图中写'物理:'就转录'物理:'，不得改写成'牛顿第二定律'）。"
             "表格用 | 分隔；公式按图中书写形式转写。只输出转录结果本身。")
def _prepare_image_b64(path, max_side=1568, quality=88):
    """压缩到长边 max_side 并转 base64 data URL（防中转丢图）。Pillow 缺失时原图直读。"""
    try:
        from PIL import Image
        img = Image.open(path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        raw = buf.getvalue()
    except ImportError:
        raw = open(path, "rb").read()
    return f"data:image/jpeg;base64,{base64.b64encode(raw).decode()}", len(raw)


def ocr_vl(path, config=None, model=None, prompt=None, max_tokens=4000):
    """视觉大模型通道（DMXAPI OpenAI 兼容）。返回 (text, meta)。"""
    import requests
    key = dmxapi_key(config)
    if not key:
        raise RuntimeError("DMXAPI key 不可用（env DMXAPI_KEY 或 .zcode/v2/config.json）")
    base = (config or {}).get("dmxapi", {}).get("base_url", "https://www.dmxapi.cn/v1")
    model = model or (config or {}).get("ocr", {}).get("vl_model", VL_DEFAULT_MODEL)
    data_url, size = _prepare_image_b64(path)
    content = [{"type": "text", "text": prompt or VL_PROMPT},
               {"type": "image_url", "image_url": {"url": data_url}}]
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}]}
    r = requests.post(f"{base}/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {key}"}, timeout=120)
    if r.status_code == 400:  # 部分模型拒绝某些参数 → 最小体重试
        r = requests.post(f"{base}/chat/completions",
                          json={"model": model, "max_tokens": max_tokens,
                                "messages": body["messages"]},
                          headers={"Authorization": f"Bearer {key}"}, timeout=120)
    r.raise_for_status()
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    return text, {"model": model, "image_bytes": size}


def ocr_easyocr(path, langs, detail=False):
    import easyocr
    reader = easyocr.Reader(langs, verbose=False)
    if detail:
        return reader.readtext(path, detail=1)
    return "\n".join(reader.readtext(path, detail=0))


def ocr_tesseract(path, langs):
    import pytesseract
    lang = "+".join(langs)
    return pytesseract.image_to_string(path, lang=lang)


def main():
    ap = argparse.ArgumentParser(description="图搜文（OCR）：vl（视觉大模型）/ easyocr / tesseract")
    ap.add_argument("image")
    ap.add_argument("--via", default="vl", choices=["vl", "easyocr", "tesseract"],
                    help="默认 vl（视觉大模型，保真最高）；easyocr/tesseract 本地免费")
    ap.add_argument("--model", help="vl 通道模型（默认 config.ocr.vl_model 或 Doubao-1.5-vision-pro-32k）")
    ap.add_argument("--prompt", help="vl 通道自定义指令（默认纯转录）")
    ap.add_argument("--lang", default="ch_sim,en", help="easyocr 语言，逗号分隔")
    ap.add_argument("--detail", action="store_true", help="JSON 输出（含置信度/坐标或模型信息）")
    args = ap.parse_args()
    config = load_config()
    langs = [x.strip() for x in args.lang.split(",") if x.strip()]

    try:
        if args.via == "vl":
            text, meta = ocr_vl(args.image, config, args.model, args.prompt)
            result = {"via": "vl", "text": text, **meta}
        elif args.via == "easyocr":
            result = {"via": "easyocr", "text": ocr_easyocr(args.image, langs, args.detail)}
        else:
            result = {"via": "tesseract", "text": ocr_tesseract(args.image, langs)}
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__} {str(e)[:150]}",
                          "建议": "vl 失败→--via easyocr（本地免费）；easyocr 失败→pip install easyocr"},
                         ensure_ascii=False))
        sys.exit(1)

    if args.detail:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(result["text"])


if __name__ == "__main__":
    main()
