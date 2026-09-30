#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共助手：config.json 读取、密钥解析、请求头与 URL 工具。

密钥安全约定（重要）：
- 读取优先级：环境变量 → secrets.json（skill 根目录，用户授权的明文密钥文件，
  专为跨 CLI 迁移设计——整个 skill 目录拷走即带走密钥）→ 各 CLI 私有配置兜底。
- secrets.json 含明文 key，仅限本机与私有迁移，不得提交公共仓库/共享。
- config.json 的 api_keys 段只允许写 "env" 占位；写明文会被告警并忽略。
- DMXAPI 兜底：`.zcode/v2/config.json` 中 name=DMXAPI 的 provider 段 apiKey。
"""
import json
import os
import re
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

DMXAPI_DEFAULT = {
    "base_url": "https://www.dmxapi.cn/v1",
    "key_source": "env:DMXAPI_KEY;.zcode/v2/config.json#DMXAPI",
}


def load_config(path=None):
    if not path:
        path = Path(__file__).resolve().parent.parent / "config.json"
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def pick(value, config, *keys, default=None):
    if value is not None:
        return value
    for k in keys:
        if k in config:
            return config[k]
    return default


def headers(config=None):
    h = dict(DEFAULT_HEADERS)
    if config:
        extra = config.get("extra_headers") or {}
        if isinstance(extra, dict):
            h.update({k: str(v) for k, v in extra.items()})
    return h


def normalize_url(url):
    """去跟踪参数与井号，用于跨引擎去重。"""
    if not url:
        return ""
    try:
        u = urlparse(url)
        # 保留的查询参数（去掉 utm_* / source / ref 等噪音）
        keep = {k: v for k, v in parse_qs(u.query).items()
                if not k.startswith("utm_") and k not in ("source", "ref", "refer", "from")}
        q = "&".join(f"{k}={v[0]}" for k, v in sorted(keep.items()))
        return f"{u.scheme}://{u.netloc}{u.path}{('?'+q) if q else ''}"
    except Exception:
        return url


# ---------- 密钥 ----------

def _secrets():
    """secrets.json（skill 根目录）：用户授权的明文密钥层，跨 CLI 迁移随目录走。
    结构：{"dmxapi": "...", "tavily": "...", "dashscope": "..."}，键名即引擎/服务名。"""
    p = Path(__file__).resolve().parent.parent / "secrets.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def dmxapi_key(config=None):
    """DMXAPI key：env DMXAPI_KEY → secrets.json["dmxapi"]
    → .zcode/v2/config.json 中 DMXAPI provider 的 options.apiKey（ZCode 私有配置兜底）。

    实测结构（2026-08-23）：config.json 顶层键为单数 `provider`，
    provider id 为 uuid，段内 name='DMXAPI'，key 在 options.apiKey，baseURL 在 options.baseURL。
    """
    env_val = os.environ.get("DMXAPI_KEY", "").strip()
    if env_val:
        return env_val
    sec = str(_secrets().get("dmxapi") or "").strip()
    if sec:
        return sec
    cand = Path.home() / ".zcode" / "v2" / "config.json"
    try:
        data = json.loads(cand.read_text(encoding="utf-8"))
        providers = data.get("provider") or data.get("providers") or {}
        if isinstance(providers, dict):
            providers = list(providers.values())
        for p in providers:
            if not isinstance(p, dict):
                continue
            opts = p.get("options") or {}
            if not isinstance(opts, dict):
                continue
            sig = str(p.get("name", "")) + str(opts.get("baseURL", ""))
            if "DMXAPI" in sig or "dmxapi" in sig.lower():
                key = opts.get("apiKey") or ""
                if key:
                    return key
    except Exception:
        pass
    return None


def api_key(name, config=None):
    """通用 API key 解析：env 优先 → secrets.json[name]（用户授权明文层）
    → config.api_keys[name] 只接受 "env" 占位（明文一律拒绝，key 请放 secrets.json）。"""
    env_map = {"tavily": "TAVILY_API_KEY", "dashscope": "DASHSCOPE_API_KEY",
               "zhipu": "ZHIPU_API_KEY", "bocha": "BOCHA_API_KEY"}
    env_val = os.environ.get(env_map.get(name, f"{name.upper()}_API_KEY"), "").strip()
    if env_val:
        return env_val
    sec = str(_secrets().get(name) or "").strip()
    if sec:
        return sec
    if config:
        v = (config.get("api_keys") or {}).get(name)
        if v and v != "env":
            print(f"⚠️ 警告：config.json 中 {name} 密钥是明文，已忽略（请移到 secrets.json 或环境变量）")
            return None
    return None


def dashscope_key(config=None):
    """百炼 key：env DASHSCOPE_API_KEY → secrets.json["dashscope"] → config.api_keys 占位链。
    （2026-09-29 起不再兜底读旧 skill dashscope-websearch 内置 key——该 skill 已退役备份至
    归档/20260929_dashscope-websearch退役备份/，key 已收编进 secrets.json，旧文件兜底属死代码。）"""
    return api_key("dashscope", config)


# ---------- 智谱 MCP broker（Streamable HTTP / SSE） ----------

BIGMODEL_MCP_BASE = "https://open.bigmodel.cn/api/mcp-broker/proxy/{service}/mcp"


def mcp_service(service_id, config=None):
    """服务名解析：config.mcp.services 可改名映射（默认即服务 ID 本身）。"""
    return ((config or {}).get("mcp") or {}).get("services", {}).get(service_id, service_id)


def mcp_broker_call(service, tool, arguments=None, timeout=90, config=None, retries=2):
    """智谱 MCP broker 通用调用（initialize → tools/call 两次无状态 POST）。

    实测要点（2026-09-27 打通 wiki / image-search 两服务）：
    - 响应为 SSE（`event: message` + `data: {...}`），同一响应混有 ping/notifications，
      **必须按 id 取自己那条**，取第一条 data 会拿到 initialize 的回包或日志。
    - **必须 r.content.decode("utf-8")**：服务端未声明 charset 时 requests 按 ISO-8859-1
      解 r.text，中文标题全乱码（mæºæ... 实测）。
    - 大响应偶发分块截断（IncompleteRead）→ 内置重试，非业务失败。
    - 业务错误走 `result.isError=true` + content[0].text（如 "Invalid image URL: ..."），
      不是 JSON-RPC error 字段，两者都要接。
    - 服务端按次计费（各服务单价见 references/engines.md），失败不扣费也不保证。

    返回 content 的文本部件列表；协议/业务错误抛 RuntimeError；缺 key 抛 KeyError。
    """
    import time as _time
    key = api_key("zhipu", config)
    if not key:
        raise KeyError("智谱 key 未配置（secrets.json['zhipu'] 或 ZHIPU_API_KEY）")
    tpl = ((config or {}).get("mcp") or {}).get("broker_base") or BIGMODEL_MCP_BASE
    url = tpl.format(service=mcp_service(service, config))
    hdr = {"Authorization": key, "Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}

    def _post(payload, rid):
        last = None
        for i in range(retries + 1):
            try:
                r = requests.post(url, json=payload, headers=hdr, timeout=timeout)
                r.raise_for_status()
                raw = r.content.decode("utf-8", "replace")
                for line in raw.splitlines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip(), strict=False)
                    except Exception:
                        continue
                    if obj.get("id") == rid:
                        return obj
                raise RuntimeError(f"MCP 响应无 id={rid} 数据行：{raw[:200]}")
            except Exception as e:
                last = e
                if i < retries:
                    _time.sleep(1.5)
        raise last

    _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                      "clientInfo": {"name": "advanced-web-search", "version": "3.0"}}}, 1)
    resp = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": tool, "arguments": arguments or {}}}, 2)
    if resp.get("error"):
        raise RuntimeError(f"MCP error: {str(resp['error'])[:160]}")
    res = resp.get("result") or {}
    parts = [p.get("text", "") for p in (res.get("content") or []) if p.get("type") == "text"]
    if res.get("isError"):
        raise RuntimeError(f"{tool}: {(parts[0] if parts else '')[:200]}")
    return parts


def mcp_json(text):
    """剥掉 MCP 文本部件的层层包装，拿到业务 dict。
    智谱服务把结果 JSON 序列化成字符串再包一层（有时还包成 {"type":"text","text":...}），
    最多剥 4 层；拿到非 JSON 内容（如 "1) Title: ..." 文本行）返回 None。
    注意 wiki 服务返回的是**被 JSON 序列化的字符串**（首字符是引号），不能只看 [ {。
    **strict=False 必需**：词条正文含未转义换行等控制字符，严格模式会
    "Invalid control character" 直接失败（2026-09-27 实测踩坑）。"""
    obj = text
    for _ in range(4):
        if isinstance(obj, str):
            s = obj.strip()
            if not s or s[0] not in '"[{':
                return None
            try:
                obj = json.loads(s, strict=False)
            except Exception:
                return None
            continue
        if isinstance(obj, dict):
            if obj.get("type") == "text" and isinstance(obj.get("text"), str) \
                    and obj["text"].strip()[:1] in "[{":
                obj = obj["text"]
                continue
            return obj
        return None
    return obj if isinstance(obj, dict) else None


def mcp_parts_map(texts):
    """图像搜索服务的部件对：返回 [(kind, payload)]，kind ∈ {text,image}。
    该服务把 [{"type":"text","text":"1) Title: ..; Link: ..; Thumbnail: .."},
              {"type":"image_url","image_url":{"url":".."}}] 再包一层 JSON 字符串下发。"""
    out = []
    for p in texts:
        try:
            o = json.loads(p, strict=False)
        except Exception:
            continue
        if isinstance(o, str):
            try:
                o = json.loads(o, strict=False)
            except Exception:
                continue
        if not isinstance(o, dict):
            continue
        if o.get("type") == "text" and isinstance(o.get("text"), str):
            out.append(("text", o["text"]))
        elif o.get("type") == "image_url":
            out.append(("image", (o.get("image_url") or {}).get("url", "")))
    return out


# ---------- 反检测浏览器（StealthyFetcher real_chrome 用） ----------

def find_chrome():
    """系统 Chrome 路径。真 Chrome 指纹比 chromium 反检测效果更好且免下载；
    patchright chromium 未装时 StealthyFetcher 必须指系统 Chrome。"""
    roots = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    for root in roots:
        if not root:
            continue
        cand = root / "Google" / "Chrome" / "Application" / "chrome.exe"
        if cand.exists():
            return str(cand)
    return None


# ---------- 代理分流（梯子引擎专用，国内引擎直连） ----------

def get_vpn_proxy(config=None):
    """返回梯子代理地址或 None。优先级：env HTTPS_PROXY → config.proxy_vpn。"""
    env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if env_proxy:
        return env_proxy
    return (config or {}).get("proxy_vpn") or None


def proxies_for(engine, config=None):
    """按引擎分流：仅在 config.vpn_engines 名单内的引擎返回梯子代理，其余直连。"""
    vpn_engines = (config or {}).get("vpn_engines") or ["brave", "ddg_html"]
    if engine not in vpn_engines:
        return None
    proxy = get_vpn_proxy(config)
    return {"http": proxy, "https": proxy} if proxy else None
