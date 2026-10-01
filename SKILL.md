---
name: advanced-web-search
description: |-
通用线上搜索与研究执行器（agent skill）：多引擎文搜网（9 引擎自动降级、--fuse RRF 融合、--cross-verify 多引擎共识检测、--days 时间过滤、查询缓存、广告软文过滤、相关性分档）、网页正文提取四层降级（web extractor，可过 Cloudflare 反爬）、B站站内数据（粉丝/播放/点赞）、文搜图、图搜图（百度识图/智谱反向图搜/区域放大/本地哈希）、OCR 图搜文三通道、文搜视频、深度研究 deep_search（拆解→并发搜索→缺口补搜→带引用报告）、可选 embedding 向量化与 rerank 重排。
Use when the user asks to: search the web for anything, verify or check up-to-date information, batch-collect links, read/extract the main content of any webpage (web extractor, including Cloudflare-protected sites), get Bilibili creator/video stats (fans, views, likes), find images from text, transcribe a photo or screenshot, reverse image search, OCR a picture, or run a multi-step deep research. Trigger even when the user does not say "search" — e.g. "帮我查一下", "查查最新", "2026 分数线", "这个截图里写的什么", "B站这个UP主多少粉丝", "这个视频多少播放", "找类似图片", "以图搜图", "深度调研", "研究一下".
---

# Advanced Web Search（通用联网搜索）

免 key 搜索执行器，不依赖付费 API 或平台 MCP 搜索工具：经内置脚本直接请求 Bing / DuckDuckGo / 百度等 HTML 通道。跨项目通用；**专门需求通过可选 `config.json` 定制（没有也无所谓，有搜索关键词就够了）。**

## 通道一览（按需直接调用）

| 需求 | 命令 | 依赖 |
|---|---|---|
| 文搜网（多引擎） | `python scripts/search.py "查询词" [--count N] [--engine tavily,bing_html,...] [--fuse 2] [--cross-verify 3] [--days 7] [--no-ads] [--no-cache] [--rerank] [--embed] [--strict] [--read N]` | requests+bs4+jieba |
| **多引擎共识检测** | `python scripts/search.py "查询词" --cross-verify 3 --engine bing_html,baidu_html,zhipu`（标注每家结果被几个引擎共同命中：high≥3/mid=2/single=孤证 + 跨引擎数字共识） | 同上 |
| **百科词条（Wikimedia）** | `python scripts/search.py "词条名" --engine wiki`（0.01元/次；实体索引非全文检索，命中窄，适合已知词条名的定名核实） | 智谱 key |
| **引擎怎么选** | 读 `references/engine-comparison.md`（多维对比+决策流程+组合配方，2026-09-29 实测） | — |
| **网页正文提取（web extractor）** | `python scripts/read_page.py <url> [url2 ...]`（bs4→Playwright→StealthyFetcher→Jina 四层自动降级） | requests+bs4；scrapling（第三层） |
| **B站站内数据** | `python scripts/platforms/bilibili.py card\|stat <UP主UID/空间URL>`；`view <BV号/视频URL>`（粉丝/播放/点赞/投币/竖横屏） | requests；playwright（view 风控兜底） |
| **文搜视频（t~v）** | `python scripts/video_search_text.py "关键词" [--count N] [--out 缩略图目录]`（Bing 视频，返回真实视频 URL/来源站/上传人/上传时间，2026-09-23 新增） | requests+bs4 |
| 文搜图 | `python scripts/image_search_text.py "关键词" [--count N] [--engine bing,baidu,zhipu] [--out 下载目录]`（双免key引擎 + 智谱 MCP 按需） | requests+bs4；智谱 key（zhipu 档） |
| 图搜文（OCR） | `python scripts/ocr_image.py 图片路径 [--via vl\|easyocr\|tesseract] [--detail]`（默认 vl 视觉大模型，保真最高） | DMXAPI（vl）/ easyocr（本地） |
| 图搜图·联网（本地图） | `python scripts/similar_image_web.py 图片路径 [--engine baidu_image,yandex] [--limit N]`（**baidu_image=百度识图 Playwright 版，2026-09 实测可用**） | playwright（系统 Chrome） |
| 图搜图·联网（公网图 URL） | `python scripts/similar_image_web.py "https://图.png" --engine zhipu_image\|zhipu_pages`；区域放大 `--engine zhipu_zoom --bbox "[x1,y1,x2,y2]"`（0.01元/次，**只吃公网可达 URL**） | 智谱 key |
| 图搜图·本地 | `python scripts/similar_image_local.py 目标图 图片目录 [--algo dhash\|phash]` | Pillow |
| 结果向量化/重排 | `python scripts/embed_results.py --file 结果.json --rerank "查询词"` 或 `--dedup` | requests（DMXAPI，默认关闭） |
| 网页截图 | `python scripts/screenshot_page.py URL 输出.png [--full] [--selector CSS] [--wait MS] [--width N]`；批量 `URL... --outdir 目录` | playwright（系统 Chrome） |
| 深度研究 | `python scripts/deep_search.py "问题" [--out 报告.md] [--rounds 2]` | DMXAPI + engines |

## 执行顺序（多引擎自动降级）

1. 按 `config.json` 的 `engine_priority` 顺序逐级尝试（默认 tavily → zhipu → bailian → enhanced_search → bing_html → baidu_html → ddg_html），**某引擎失败自动落到下一个**，`skipped` 字段记录每个失败原因。
2. **推荐 `--fuse 2`**：并发前 N 个可用引擎 + RRF 融合排序（多引擎互相纠偏，单引擎垃圾被稀释，比单引擎顺序降级质量高一档）。
3. **关键数据核验用 `--cross-verify N`**：多引擎共识标注（高/中/孤证）+ 跨引擎数字共识；**别用默认池跑**（前3个是付费引擎），配 `--engine bing_html,baidu_html,zhipu`。
4. **相关性如实上报**：输出含 `relevant_count`（与查询实词高度相关的条数）和每条 `relevance: high/low`；`relevant_count=0` 时给 `warning` 显式告警——**看到 warning 先换查询词/换引擎，别硬编结论**。`--strict` 只保留 high 条目。
5. **招生广告/软文自动打标**（默认开）：命中多项营销信号的条目带 `ad_suspect/ad_signals` 并下沉到末尾；找学习资料类查询加 `--no-ads` 直接丢弃。
6. **看 `quality_notes`**：出现"某引擎结果被单一域名占榜"= 该引擎排序被 SEO 农场污染（bing 实测有），本次别用该引擎结论，换查询说法复核。
7. 跨引擎结果按 URL 规范化合并去重，`rescore_domains` 命中的域名排前。
8. 显式指定：`--engine tavily,bing_html`（按序仅用这两个）；纯免 key：`--engine bing_html,baidu_html`。
9. 大批量（>10 条）：轮换查询变体多次调用，参考 `references/engines.md` 分页表。
10. 高价值结果用 `--read N` 或 read_page.py 深读，精炼回传。

**场景路由（详见 references/engines.md 路由表）**：官方页刚需/数据核验 → 默认或 --fuse 2-3；日常中文泛查询 → `--engine bing_html,baidu_html --fuse 2`（零成本，别烧 tavily 额度）；bailian 与 enhanced 同账号功能重叠，普通查询二选一不同时 fuse；百科定名/背景 → `--engine wiki`（0.01元/次）。

## 通用汇报原则

- 结论先于过程：核心发现（3-5 句）+ 要点（带来源域名），再给细节。
- 每条结果标来源 URL；时效性信息标"截至搜索时间"。
- 来源可信度分级（gov/edu > 主流媒体 > 专业平台 > 个人）：`references/engines.md`。
- 不整段粘贴原文；失败如实说"未找到/通道不可用"，并给降级或替代路径，不编造。

## 平台站内数据适配器（B站）

search.py 只能拿二手文章；**粉丝数/播放量等站内数据直连平台接口**：

```bash
python scripts/platforms/bilibili.py card 517327498          # UP主：昵称/签名/粉丝数/投稿数/等级
python scripts/platforms/bilibili.py stat space.bilibili.com/517327498   # 粉丝/关注（relation/stat）
python scripts/platforms/bilibili.py view BV1GJ411x7h7       # 单视频：播放/点赞/投币/收藏/分享/弹幕/评论/时长/竖横屏
```

- 实测口径（2026-09-12）：card/stat 免签名直连；**view 接口有风控（412），自动 Playwright 兜底**（`via: "playwright"` 字段如实标注来源路径），首次会访问主站拿 buvid3 cookie。
- 站内搜索（wbi 签名）未实现，返回 `not_implemented` 并给替代方案（`search.py "关键词 site:bilibili.com"` 或 card/view 直连）。
- 高频调用会触发风控（-352/-412），串行+间隔使用；脚本已内置 0.3s 间隔。

## 三模态要点

- **文搜图**：先不带 `--out` 拿 URL 列表人工挑；确定要存的图再 `--out` 批量下载并校验大小 >0。每张图的来源页在 `source_page` 字段。
- **图搜文（OCR）**：默认 `--via vl`（视觉大模型，手写体/公式/复杂排版最强，自动压缩大图防中转丢图）；纯印刷体批量用 `--via easyocr`（零成本）。⚠️ 视觉模型转录存在随机性（同图两次输出可能不同），关键内容需二次核对；prompt 已强化"禁止改写/补充/推断标签"。
- **图搜图（本地图）**：**百度识图 2026-09-13 实测可用**——Playwright 完整上传交互（set_input_files）绕过旧 upload API 的 JS token 门槛，`/ajax/picsimi` 接口返回 30 条结构化相似图（缩略图 URL+尺寸），页面另带"相关商品/图片来源"；默认引擎已切新版。Yandex 需梯子+上传入口改版（弱），保留降级；搜狗识图已移除（入口下线，2026-09-29 清理）。**备用路径**：① 图搜文（OCR 提取关键词）→ 文搜图 ② 本地相似检索（题图用 dhash，照片 phash）。
- **图搜图（公网图 URL，智谱 MCP 2026-09-27 新增）**：`--engine zhipu_image`（反向图搜）/ `zhipu_pages`（图出处）/ `zhipu_zoom --bbox`（区域放大），均 0.01 元/次、秒级返回（对比百度识图 Playwright 约 8s）。**只吃公网可达的图片 URL**——本地文件与 `data:` URI 均被服务端拒（实测），被墙站点图源（如 upload.wikimedia.org）服务端 HEAD 取不到同样被拒；本地文件仍走百度识图，或先上传图床拿 URL。返回含 `kind=visual_meta` 项（best_guess_labels + 带置信分的视觉实体）。⚠️ 中文图源命中弱（实测返回泛化英文实体），偏国际/英文图源。
- **文搜图**：`--engine zhipu` 可加智谱 MCP 一路（最多 5 条/次，0.01元/次）；注意其图片 URL 是**智谱 CDN 代理链**（带签名有效期）不是原图源站，且无 source_page——需要图源出处时用默认的 bing/baidu 引擎。

## 结果后处理：embedding 与重排（默认关闭）

- **--rerank**：`search.py "查询词" --rerank` —— **/v1/rerank 真端点**（qwen3-rerank，2026-09-12 实测 200，分数区分度好）；端点失败自动回退查询-结果余弦重排。
- **--embed**：批量为结果附加向量，供语义去重/聚类（独立去重：`embed_results.py --dedup`，相似度 >0.92 判重）。
- 独立用法：`python scripts/embed_results.py --file 结果.json --rerank "查询词"`。
- key 来源：env `DMXAPI_KEY` → 自动读 `.zcode/v2/config.json` 中 DMXAPI provider（运行时读取，不落盘）。

## 查询操作符速查（Bing/Google/DDG 通用）

`"精确短语"` · `site:` · `-site:` · `filetype:pdf` · `intitle:` · `inurl:` · `intext:` · `OR` · `-排除词` · `*通配符` · `( )分组` · 时间用 `freshness`/`date_after`/`date_before`。

把操作符直接写进查询词即可，脚本不单独解析。完整表见 `references/engines.md`。

## 常见失败与修复

| 现象 | 处理 |
|---|---|
| 403/反爬页 | read_page 会自动降级到 StealthyFetcher 层（系统 Chrome 反检测，治 Cloudflare 类，慢 15-30s）；仍失败 → 等 30-60 秒重试；换引擎 |
| Bing 零结果 | 自动降级 DDG；仍无 → 换更通用查询词，先简后繁 |
| relevant_count=0 / warning 出现 | 查询词与结果全部不相关——换词重搜或 --fuse 多引擎，**别硬编结论** |
| B站 view 返回 412/风控 | 脚本自动 Playwright 兜底；仍失败 → 间隔几分钟再试（风控冷却） |
| 图搜图联网不可用 | 走降级链（OCR→文搜图 / pHash），不报错阻塞任务 |
| OCR 空输出 | 查清晰度/旋转/字号；长图切块；换 `--via tesseract` |
| 智谱图搜图报 `Invalid image URL` | 该通道只吃**公网可达的图片 URL**：本地文件/data:URI 被服务端拒；被墙站点图源 HEAD 超时（10s）也被拒。本地图改用 `baidu_image`，或先传图床拿 URL |
| 智谱 MCP 返回中文乱码（mæºæ…） | 已修（`r.content.decode("utf-8")`）；如再出现说明又用了 `r.text`（requests 默认按 ISO-8859-1 解未声明 charset 的响应） |
| 智谱 MCP 报 `IncompleteRead`/分块截断 | 大响应偶发，`mcp_broker_call` 内置重试；仍失败重跑一次即可 |
| wiki 查询零结果 | 它是**实体词条索引不是全文检索**（"赋分制""2026分数线"这类查不到词条）——换通用引擎，别当引擎故障 |
| `quality_notes` 报某引擎被单一域名占榜 | 该引擎本次排序被 SEO 农场污染（bing 实测），别采信其结论；换查询说法（拆词加空格常有效）或换引擎复核 |
| `--days` 结果全带 `url_missing` | 智谱 recency 模式服务端不回 link（裸测定位，2026-09-29）——标题/日期可用，拿标题回搜溯源；或换 tavily `--days` |
| 缓存返回了旧结果 | 输出 `cache.hit=true` 带 `age_minutes`；时效敏感查询加 `--no-cache`，或调小 `config.cache.ttl_hours` |
| config.json 改了没生效 | 确认在 skill 根目录（与 SKILL.md 同级）、JSON 无尾逗号；CLI 参数优先于 config |

## 自定义设置（可选 config.json 定制层）

与 SKILL.md 同级的 `config.json`，字段全可选、缺啥用啥默认值；没有该文件（或留 `{}`）全部走内置默认——**零配置即可用**。专门需求在此调，别改脚本：

```json
{
  "engine_priority": ["tavily", "zhipu", "bailian", "enhanced_search", "bing_html", "baidu_html", "ddg_html"],
  "default_count": 10,
  "default_language": "zh",
  "timeout_seconds": 15,
  "download_dir": "C:/temp/images",   // 文搜图 --out 默认目录
  "ocr_langs": ["ch_sim", "en"],
  "rescore_domains": ["gov.cn", "edu.cn"],  // 来源域名优先排序
  "block_domains": [],                   // 域名黑名单（命中即剔）
  "extra_headers": {},                   // 对抗特定站点反爬的补充头
  "api_keys": { "tavily": "env", "dashscope": "env" },  // 只写 env 占位，明文会被忽略
  "proxy_vpn": "http://127.0.0.1:7890",  // 梯子代理（vpn_engines 名单内引擎走它，其余直连）
  "vpn_engines": ["ddg_html"],
  "tavily": { "api_base": "https://api.tavily.com", "search_depth": "basic" },  // 端点/深度可换
  "dashscope": { "mcp_base": "https://dashscope.aliyuncs.com/api/v1/mcps" },    // bailian/enhanced 共用前缀
  "cache": { "enabled": true, "ttl_hours": 6, "path": "" },  // 查询缓存（sqlite）；时效敏感 --no-cache 或调小 ttl
  "cross_verify": { "engines": 3, "title_overlap": 0.6, "min_number_engines": 2 },  // --cross-verify 默认并发引擎数/标题归并阈值/数字共识门槛
  "ad_filter": { "enabled": true, "min_signals": 2,     // 招生广告/软文：>=2 组信号才判；只打标下沉，--no-ads 才删
                 "keywords": [], "strong_keywords": [], "brand_keywords": [],  // 在内置词表之上追加
                 "whitelist_domains": [] },             // 在内置 gov.cn/edu.cn/chsi.com.cn/cqksy.cn 等之上追加豁免域
  "mcp": { "broker_base": "https://open.bigmodel.cn/api/mcp-broker/proxy/{service}/mcp",
           "services": { "wiki": "wiki", "image-search": "image-search" } },  // 智谱 MCP（用 secrets.json 的 zhipu key）
  "embed": { "enabled": false, "model": "text-embedding-3-large", "batch": 50 },
  "rerank": { "enabled": false, "model": "qwen3-rerank", "topk": 20 },  // /v1/rerank 真端点，失败自动回退余弦
  "image_engines": ["zhipu_image", "baidu_image", "yandex"],  // zhipu_image 仅对公网图 URL 生效（本地文件自动跳过）
  "deep_search": { "planner_model": "deepseek-v4.1-flash", "writer_model": "deepseek-v4.1-flash",
                   "max_llm_calls": 4, "max_engine_calls": 12, "rounds": 2, "fuse": 2 }  // 大小杯当前统一 deepseek-v4.1-flash（2026-09-13 用户指令）
}
```

## 依赖清单（可选层，缺哪层哪层自动跳过）

- 必需：requests、beautifulsoup4、jieba（相关性分档）
- 增强层：scrapling[fetchers]（百度 TLS 指纹引擎 + read_page 第三层反检测；用系统 Chrome 免下 chromium：`common.find_chrome()` 自动探测）、playwright（read_page 第二层 + B站 view 兜底；需 `playwright install chromium`）、easyocr（OCR）、Pillow（本地图搜图）
- API 层：TAVILY_API_KEY / DASHSCOPE_API_KEY 环境变量、DMXAPI key（env 或 .zcode/v2/config.json；**OCR vl 通道与 deep_search/rerank 共用**）、智谱 key（`secrets.json["zhipu"]`；**zhipu 引擎 + wiki 百科 + 图像搜索 MCP 共用，均按次计费 0.01 元起**）

## 密钥存放（跨 CLI 迁移）

读取优先级：**环境变量 → `secrets.json`（skill 根目录）→ 各 CLI 私有配置兜底**。

- `secrets.json` 与 SKILL.md 同级，结构 `{"dmxapi": "...", "tavily": "...", "dashscope": "..."}`，键名即服务名。
- **换 CLI 迁移时把整个 skill 目录拷走即带走密钥**，新环境无需重配 env。
- ⚠️ 该文件含明文 key：仅限本机与私有迁移，**禁止提交公共仓库/共享/截图**。
- env 存在时优先于 secrets.json（不破坏已有部署）。

> 与工作流规范的关系：本 skill 只负责"执行与结果质量"；各工作流的特有约束（如数据年份、轮次预算、引用格式）由对应 AGENTS/规则文件注入管理，不在本文件重复。
