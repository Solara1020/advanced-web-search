#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询结果缓存（sqlite，默认 TTL 6 小时）。

用途:
  省 paid 引擎的按次费用 + 同题重复查询免重跑 --read/--rerank 等重后处理。
  缓存整个 search.py 的输出 JSON（含 consensus/ad_filter 等标注）。
用法（search.py 已接线，一般不直接调）:
  from cache import key, get, put
  k = key("search", query, engines_str, count, fuse, cv, days, flags..., cfg_fp)
  hit = get(k, config)          # -> (payload_dict, stored_at_ts) 或 None
  put(k, payload_dict, config)  # 空结果/异常不缓存
依赖: 仅标准库（sqlite3/hashlib/json/time）。
说明:
  - 默认库文件在 skill 根目录 cache.db（config.cache.path 可改）；cache.db 含查询内容，
    **已列入 .gitignore，禁止提交/分享**。
  - 缓存层任何故障（库损坏/锁/磁盘）都静默返回 miss，不阻塞搜索。
  - 时效敏感查询：--no-cache 旁路，或 config.cache.ttl_hours 调小。
  - 空结果不缓存（多为瞬时反爬/引擎抖动，缓存住会误当"确实没有"）。
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path


def _cfg(config):
    c = (config or {}).get("cache") or {}
    return {"enabled": c.get("enabled", True),
            "ttl": float(c.get("ttl_hours", 6)) * 3600,
            "path": c.get("path") or ""}


def _db(config):
    p = Path(_cfg(config)["path"]) if _cfg(config)["path"] \
        else Path(__file__).resolve().parent.parent / "cache.db"
    con = sqlite3.connect(str(p), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS cache "
                "(k TEXT PRIMARY KEY, stored_at REAL, payload TEXT)")
    return con


def key(*parts):
    """任意可序列化部件 → 稳定 sha1 键。"""
    return hashlib.sha1("\x1f".join(str(x) for x in parts).encode("utf-8")).hexdigest()


def get(k, config=None):
    """命中返回 (payload_dict, age_minutes)；过期/未命中/故障返回 None（顺手清过期行）。"""
    if not _cfg(config)["enabled"]:
        return None
    con = None
    try:
        con = _db(config)
        row = con.execute("SELECT stored_at, payload FROM cache WHERE k=?", (k,)).fetchone()
        if not row:
            return None
        stored, payload = row
        age = time.time() - stored
        if age > _cfg(config)["ttl"]:
            con.execute("DELETE FROM cache WHERE k=?", (k,))
            con.commit()
            return None
        return json.loads(payload), round(age / 60, 1)
    except Exception:
        return None
    finally:
        if con:
            con.close()


def put(k, payload, config=None):
    """存结果并顺手清理过期行；空结果与任何故障都静默跳过。"""
    if not payload or not payload.get("items"):
        return
    con = None
    try:
        con = _db(config)
        now = time.time()
        con.execute("INSERT OR REPLACE INTO cache(k, stored_at, payload) VALUES (?,?,?)",
                    (k, now, json.dumps(payload, ensure_ascii=False)))
        horizon = now - max(_cfg(config)["ttl"], 3600) * 2  # 过期两倍时长的行直接清
        con.execute("DELETE FROM cache WHERE stored_at < ?", (horizon,))
        con.commit()
    except Exception:
        pass
    finally:
        if con:
            con.close()
