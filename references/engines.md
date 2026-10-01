# 引擎细节参考（按需读取）

## 引擎清单与费用（2026-09-27）

| 引擎 | 通道 | 费用 | 本机可达性 | 备注 |
|------|------|------|-----------|------|
| tavily | API | 免费 1000 次/月（超出付费档，推荐量级下不需要） | ✅ 实测可达 | 质量最高档；需 `TAVILY_API_KEY` |
| zhipu | API | 0.01 元/次 | ✅ 实测可达 | search_std；带 publish_date；count 1-50；支持时间/域名过滤 |
| zhipu_sogou | API | 0.05 元/次 | ✅ 实测可达 | 补知乎/腾讯生态；不进默认池，按需 `--engine zhipu_sogou` |
| bailian | MCP | 按次计费（控制台口径） | ✅ 实测可达 | 中文质量好；需 `DASHSCOPE_API_KEY` |
| enhanced_search | MCP | 按次计费（控制台口径） | ✅ 实测可达 | IQS 增强版，固定 10 条 |
| wiki | MCP（智谱 broker） | 0.01 元/次 | ✅ 2026-09-27 实测 | **Wikimedia 百科词条**（非全网检索）；按需 `--engine wiki`，不进默认池 |
| bing_html | HTML | 0 | ✅ 可达（302→cn.bing.com） | 中文复合词须加引号（关键词易被拆词）；⚠️部分查询整页被 SEO 农场占榜，见下文 |
| baidu_html | HTML | 0 | ✅ 可达 | Scrapling TLS 指纹版；`mu` 属性直出真实 URL |
| ddg_html | HTML | 0 | ❌ 默认被墙，开梯子后可用 | 免 key 兜底 |
| yandex 图搜 | HTML | 0 | ⚠️ 弱 | 需梯子 + 入口改版 |
| 百度识图 | HTML | 0 | ✅ 可达 | Playwright 上传；本地文件唯一稳定通道 |
| 智谱图像搜索 | MCP（智谱 broker） | 0.01 元/次 | ✅ 2026-09-27 实测 | 5 工具（文搜图/反向图搜/图出处/区域放大/文本网搜）；**只吃公网图片 URL** |

> 结论：默认优先级 tavily → zhipu → bailian → enhanced_search → bing → baidu → ddg；缺 key 的引擎自动跳过，永远有免 key 引擎兜底。**sogou_html 与搜狗识图已于 2026-09-29 移除**（前者常态验证码无法自动化，后者上传入口下线）。

## 引擎适用场景

| 引擎 | 适用场景 | 备注 |
|------|---------|------|
| Bing（默认） | 综合、中英、学术/技术 | search.py 主通道，HTML 解析 |
| DuckDuckGo | 免 key 降级、隐私 | html.duckduckgo.com，反爬时自动降级 |
| 百度 | 中文内容、国内政策/新闻 | 查询词加 `site:gov.cn` 等；本 skill 无直达通道，用 query 操作符逼近 |
| Google/Perplexity 等 | 需要 API key 的通道 | 免 key 模式下不建议作为主通道 |

> 用户未指定时按查询语言判断：中文查询倾向 Bing(mkt=zh-CN)/DDG，英文查询同。

## 通用查询操作符（Bing/Google/DDG 大部分支持）

| 操作符 | 功能 | 示例 |
|--------|------|------|
| `"精确短语"` | 完全匹配，不拆词 | `"deep learning transformer"` |
| `site:` | 限定站点 | `site:edu.cn 高考政策` |
| `-site:` | 排除站点 | `-site:csdn.net 算法` |
| `filetype:` / `ext:` | 限定文件类型 | `filetype:pdf 量子力学讲义` |
| `intitle:` | 标题包含 | `intitle:2025高考志愿` |
| `inurl:` | URL 包含 | `inurl:admission 大学招生` |
| `intext:` | 正文包含 | `intext:新高考选科要求` |
| `OR` / `\|` | 或逻辑 | `物理 OR 化学 选科` |
| `-` | 排除词 | `苹果 -手机` |
| `*` | 通配符 | `site:gov.cn * 高考改革` |
| `( )` | 分组 | `(物理 OR 化学) site:edu.cn` |
| `AROUND(n)` | 两词相距不超过 n 词（Google） | `education AROUND(5) policy` |
| `loc:` | 限定地区（Bing） | `loc:cn 招生` |

## 时间/语言/地区过滤

写入查询词（脚本不单独解析参数）：

| 需求 | 写法 |
|------|------|
| 最近 24 小时 | `freshness:day` 或直接加日期词 |
| 最近一周 | `freshness:week` |
| 最近一个月 | `freshness:month` |
| 最近一年 | `freshness:year` |
| 自定义范围 | `date_after:"2025-01-01"` + `date_before:"2025-06-30"` |
| 中文内容 | `mkt=zh-CN`（search.py 内置）；查询词加 `语言:zh` 不通用，直接加中文关键词 |
| 特定国家 | `site:.cn` / `site:.edu.cn` 等后缀过滤 |

## 大批量采集（>10 条）分页变体

单次调用默认 10 条。要 11-200 条：按"变体轮换"多调几次，URL 去重合并。变体维度：

| 维度 | 示例 |
|------|------|
| 同义词 | `高考` → `大学入学考试` → `college entrance exam` |
| 站点轮换 | `site:edu.cn` → `site:gov.cn` → `site:news.cn` |
| 时间分段 | `freshness:month` → `date_after:"2025-05-01"` → `date_after:"2025-03-01"` |
| 语言/词族 | 英文词 → 中文词 → 缩写词 |
| 操作符调整 | 去掉 `intitle:` → 去掉 `filetype:` → 逐步放宽 |

档位参考：

| 目标 | 策略 |
|------|------|
| 1-10 条 | 单次调用 |
| 11-30 条 | 3 轮不同变体 |
| 31-50 条 | 5 轮变体 |
| 51-200 条 | 10-20 轮变体；可两引擎平分、分批报告，避免一次塞太多结果进上下文 |

> 大批量时务必逐步汇报（先给 top 10 + 变体计划），一次 200 条结果原文回传会撑爆上下文。

## 常见搜索模板

1. **官方/政策**：`site:gov.cn OR site:edu.cn "关键词" filetype:pdf` + 时间过滤
2. **学术**：`"论文标题" filetype:pdf (site:edu.cn OR site:arxiv.org) -site:csdn.net`
3. **新闻/时事**：`"事件关键词"` + `freshness:week` + `-广告 -推广`
4. **技术**：`"error message" (site:stackoverflow.com OR site:github.com)`
5. **交叉验证**：两引擎各搜一次，合并去重，按可信度排序

## 来源可信度评级

| 等级 | 来源类型 | 示例 |
|------|---------|------|
| ⭐⭐⭐⭐⭐ | 官方政府/机构 | gov.cn、教育部、教育考试院 |
| ⭐⭐⭐⭐ | 学术/教育机构 | edu.cn、arxiv、知网 |
| ⭐⭐⭐ | 主流媒体 | 新华社、人民日报、BBC |
| ⭐⭐ | 专业平台 | 知乎高赞、StackOverflow |
| ⭐ | 个人/自媒体 | 博客、公众号 |
| ⚠️ | 低质内容站 | 内容农场、采集站（`rescore_domains` 可配合排除） |

## 搜索词优化技巧

1. 从宽到窄：关键词原样 → 无结果再拆词 → 加引号精确
2. 中英对照：概念词同时搜中英文
3. 同义词扩展：`A OR B OR C`
4. 排除噪音：无关高频词用 `-`
5. 逐步精炼：根据第一轮结果调整操作符
6. 反爬回避：中文词更易触发反爬；换英文词或重试等待 30-60 秒

## 场景路由表（按信息类型选引擎，资源最大利用）

成本层级：**免 key 引擎**（bing/baidu/ddg，无限用）→ **tavily**（免费 1000 次/月，质量最高，额度有限）→ **dashscope 按次付费**（bailian/enhanced）。

| 信息类型 | 推荐调用 | 理由 |
|------|---------|------|
| **官方/权威信息**（政府政策、法规、行业标准、机构公告——任何"必须来自官方"的场景） | 默认（tavily 优先）或 `--engine tavily,bailian --fuse 2`；时效性内容查询词带年份/地域 | tavily 对官方站点命中率最高；`rescore_domains`（gov.cn/edu.cn，可在 config 改）自动置顶权威域 |
| **要引用的数字/事实核验** | `--cross-verify 3 --engine bing_html,baidu_html,zhipu` | 多引擎共识（consensus.level）+ 跨引擎数字共识，直接看"N 家共同命中"；比 `--fuse 3` 多一层共识标注 |
| **概念/名词的定名与背景**（某人是谁、某事件的百科级背景） | `--engine wiki`（0.01元/次）或 wiki + 通用引擎并看 | Wikimedia 词条权威且无广告；但它**不是全文检索**，命中窄、排序偶不准——通用检索面仍靠默认引擎 |
| **日常中文泛查询**（概念解释、背景知识、科普） | `--engine bing_html,baidu_html --fuse 2` | **零成本**——别烧 tavily 额度和 dashscope 余额 |
| **英文/国际内容** | 默认（tavily）或 `--engine ddg_html`（需梯子） | tavily 英文索引强；ddg 补国际面 |
| **技术/开发问题**（报错、库用法、API） | 默认（tavily）或 `--engine bing_html,ddg_html`；报错信息直接作英文查询词 | stackoverflow/github 等英文技术面；免 key 可先试 bing |
| **时效新闻/热点追踪** | `--engine enhanced_search,bailian --fuse 2` + 日期词 | 中文新闻面强；两者同账号功能重叠，普通场景二选一即可 |
| **专业领域深检索**（金融/法律/医疗等垂直内容） | enhanced_search 或 tavily，配合 `site:` 限定权威域 | enhanced 权威召回有加成；site: 操作符直接精确限定 |
| **大批量采集（>20 条）** | tavily `--count 20` + 查询变体轮换；中文源用 bailian/enhanced 轮换 | bailian/enhanced 固定 ≤10 条/次 |
| **平台站内数据**（粉丝数/播放量等） | `platforms/` 适配器（现有 bilibili.py） | 站内数据搜索引擎拿不到二手准确的，直连平台接口 |
| **深度研究（多跳问题）** | `deep_search.py`（自动 fuse 2 + 守门分档 + 缺口补搜） | 无需手动选引擎 |
| **需要正文/深读** | `--read N`（≤3）或 read_page.py 单独调用 | 四层降级，能过 Cloudflare |

**预算纪律（通用）**：①tavily 额度留给官方页/英文/验证三类它不可替代的场景，免 key 引擎能答的别用它；②bailian 与 enhanced 同账号同返回结构，普通查询二选一，不同时 fuse（花两份钱买同一面）；③（sogou 已移除，原"凑数引擎"条目作废）。

## 引擎实测边界（2026-09-12 v2.1 定期实测）

| 引擎 | 状态 | 说明 |
|------|------|------|
| tavily | ⭐⭐⭐⭐⭐ | 官方页命中率最高，key 已配 |
| bailian | ⭐⭐⭐⭐ | 中文质量好，key 已配（旧文件兜底） |
| zhipu | ⭐⭐⭐⭐ | 智谱 search_std（0.01元/次）：返回带 **publish_date** 发布时间字段；支持 count(1-50)/时间过滤/域名过滤/content_size。⚠️坑：①count 不按传入值精确返回（请求10返15或50，客户端截断兜底）②link 有邮箱畸形/全空条目（已过滤）③`search_intent=true` 会致 0 条（引擎恒传 false） |
| zhipu_sogou | ⭐⭐⭐ | 智谱 search_pro_sogou（0.05元/次）：域名面宽（央媒/教育垂直/gov.cn/toutiao/sohu）。⚠️**知乎覆盖：官方明确宣称可抓，我方 2026-09-23 多条件实测（3 种域名格式 × 2 种 intent × 多次查询）全为 0 条；对照组 www.sohu.com/www.toutiao.com 各 15 条证明 domain_filter 机制正常——宣传与实测冲突，并列如实记录**。默认池不启用（按需调用） |
| bing_html | ⭐⭐⭐⭐ | 引号严格查询会给推荐卡（守门已治）；别给它加引号。**2026-10-01 修复整页离题**：cookie 预热 + form=QBRE（A/B 实测有效，正常查询结果不变） |
| baidu_html | ⭐⭐⭐⭐ | **2026-09 根治**：Scrapling TLS 指纹 + `mu` 属性直出真实 URL + class-free 摘要（容器文本剥标题，免疫改版）。此前 content-right_8Zs40 类名失效致空摘要 |
| ddg_html | ⭐⭐⭐ | 需梯子（vpn_engines 自动分流） |
| enhanced_search | ⭐⭐⭐⭐ | 百炼 EnhancedSearch（search_pro）：10/10 全相关无脏结果，摘要全非空；⚠️"权威站点优先"实测打折——来源偏 sohu/toutiao 自媒体，政府页命中反不如 tavily；固定 10 条无 count 参数；与 bailian 基础版（有脏结果风险）fuse 互补好 |
| **wiki** | ⭐⭐⭐⭐（限定名类） | **Wikimedia 百科词条，2026-09-27 接入实测**：中英文查询均可（英文词返回中文条目），`articleBody` 完整，另有 `official_url`（词条官方站）。⚠️两点如实记录：①**命中窄且排序偶不准**（查"高考"首位返回"俄罗斯高考"、"Newton"返回"曼联"、查"赋分制"零结果）——它是**实体/词条索引不是全文检索**，只适合"已知词条名的定名与背景核实"，开放式检索请用通用引擎；②服务在智谱侧，源站被墙不影响本通道。按需 `--engine wiki`，0.01 元/次 |

### bing_html 的 SEO 农场占榜问题（2026-09-27 实测，可复现）

同一引擎对**不同说法**的同题查询质量可能天差地别：

| 查询 | 返回 | 判定 |
|------|------|------|
| `重庆高考报名时间` | 整页 `www.ditu.me`（地图迷，行政区划/卫星图内容） | ❌ 排序被 SEO 农场污染，两次复测一致 |
| `重庆 高考 政策` | `cq.gov.cn` / `jw.cq.gov.cn` / `cqzk.com.cn` / `cqksy.cn` | ✅ 官方站优先，正常 |
| `2026 重庆 高考 报名 时间` | 整页"国务院节假日安排/日历"类无关结果 | ❌ 跑题（靠守门剔除） |

**对策（已实现，两层）**：①**根治（2026-10-01）**：cookie 预热 + `form=QBRE`——A/B 实测把"整页华为官网"级离题恢复正常，正常查询结果不变（见 TROUBLESHOOTING.md）；②`quality_notes` 单一域名占榜检测（按引擎分组，≥70% 且 ≥4 条告警）保留兜底。遇告警仍可换查询说法（拆开加空格）或换引擎复核。

## read_page 四层与反爬

1. bs4 直连（国内站首选）→ 2. Playwright 渲染（JS 页）→ 3. **StealthyFetcher 反检测**（Cloudflare 类，2026-09-12 实测攻克 Medical Daily；patchright chromium 未装时自动用系统 Chrome `real_chrome=True`，由 `common.find_chrome()` 探测）→ 4. Jina（显式 --jina，国际站）。

## 分页与大批量（tavily/bailian）

- tavily：`max_results` 上限 20；翻页用查询变体轮换
- bailian：`count` 上限 10；大批量同样轮换查询词

## 图片与多模态通道实测（2026-09-13）

| 通道 | 状态 | 说明 |
|------|------|------|
| 文搜图 | ✅ 双引擎 | Bing（a.iusc m 属性 JSON）+ 百度图片 acjson 接口（免 key，15/15）；默认合并去重、引擎交错排列保图源多样性 |
| 图搜图·百度识图 | ✅ **可用** | Playwright set_input_files 完整上传 → /ajax/picsimi 结构化 JSON（30 条相似图）；旧 upload API 撞 JS token 已绕过 |
| 图搜图·Yandex | ⚠️ 弱 | 需梯子 + 上传入口改版（需交互定位）；保留降级 |
| 图搜图·本地 | ✅ 稳定 | dhash（题图/文字）/ phash（照片） |
| OCR | ✅ 三通道 | vl 视觉大模型（豆包默认，保真最高；⚠️ 输出有随机性，关键内容二次核对）/ easyocr / tesseract |
| 截图 | ✅ | --full/--selector/--wait/--width/批量；系统 Chrome |

## 智谱图像搜索 MCP（2026-09-27 接入实测）

服务名 `image-search`（broker 路径 `.../proxy/image-search/mcp`，用智谱 key，**5 工具均 0.01 元/次**）：

| 工具 | 接入位置 | 入参 | 实测状态 |
|------|---------|------|---------|
| `search_image` | `image_search_text.py --engine zhipu` | query（仅此一个） | ✅ 可用，单次最多 5 条；**返回的图片 URL 是智谱 CDN 代理链**（`qc4n.bigmodel.cn/xxx_searched.png?UCloudPublicKey=…&Signature=…`），不是原图源站，带签名有效期；无 source_page（出处可从标题里的人工痕迹推，如"_红动中国"） |
| `image_search_with_image` | `similar_image_web.py --engine zhipu_image` | img_url | ✅ 可用，返回 Google-Lens 系结构：full/partial/visually_similar_images + pages_with_matching_images + web_entities（带置信分）+ best_guess_labels（落成一条 kind=visual_meta 项） |
| `text_search_with_image` | `similar_image_web.py --engine zhipu_pages` | img_url | ✅ 可用，以"含图网页"为主，偏图出处溯源 |
| `image_zoom_in_search_tool` | `similar_image_web.py --engine zhipu_zoom --bbox "[x1,y1,x2,y2]" [--ref-name X]` | img_url + bbox(0-999) + ref_name | ✅ 可用（bbox 裁区域后再搜，整图搜不到时框局部） |
| `web_search` | **未接入** | query + page_num | 文本网页搜索 5 条/页，**与 zhipu search_std 功能重复**，按"引擎池不重复占位"原则不接入 |

**关键约束（务必先看这条）**：图搜图三工具**只吃公网可达的图片 URL**——
① 本地文件不支持：`data:` URI 实测被服务端直接拒（"Invalid image URL: Invalid URL format"）；
② 服务端会先对 URL 发 HEAD 校验（10s 超时即拒），**被墙站点图源不可用**（实测 `upload.wikimedia.org` 超时被拒）；
③ 所以本地图仍走 `baidu_image`（Playwright 上传，免费但约 8s），或先把图传到任意公网图床拿 URL。
④ 内容质量如实记录：对中文图源（重庆大学校门）实测返回泛化英文实体（"Urban area/Metropolitan area"）与 YouTube 页，`full_matching_images` 为空——**中文图源命中弱，偏国际/英文图源**；整图搜不到时用 `zhipu_zoom` 框局部再试。

## 结果质量层（2026-09-27 新增）

三条独立机制，都在 search.py 结果层，全部"只打标/告警，不静默删"：

| 机制 | 触发 | 输出字段 | 用途 |
|------|------|---------|------|
| `--cross-verify [N]` | 并发 N 个引擎（默认 config.cross_verify.engines=3）同题比对 | 每条 `consensus{n, engines, level}`；顶层 `cross_verify{high/mid/single, clusters, number_consensus}` | 标出"N 家引擎共同命中"（`high`≥3 家 / `mid`=2 家 / `single`=孤证）；`number_consensus` 列跨引擎共现的数字（分数线/人数/时间类数据点的可信度参考） |
| 广告过滤（默认开） | 多信号命中 ≥2 组：营销话术/违规承诺/联系方式/价格费用/机构特征/机构品牌 | 每条 `ad_suspect/ad_signals/ad_hits`；顶层 `ad_filter{flagged, dropped}` | 命中项**打标并下沉到末尾**，默认不删；`--no-ads` 才丢弃 |
| 单一域名占榜告警 | 某引擎结果单一域名 ≥70% 且 ≥4 条 | 顶层 `quality_notes[]` | 发现"引擎侧排序被 SEO 农场污染"（bing 实测案例见上） |

**用法建议**：
- 关键数据核验：`--cross-verify 3 --engine bing_html,baidu_html,zhipu`（3 家含 1 家付费，约 0.01 元/次；**别用默认池跑 --cross-verify，前 3 个是 tavily/zhipu/bailian 全付费**）
- 找学习资料这类泛查询：加 `--no-ads` 直接滤掉招生软文
- **共识的现实预期**：实测 3 引擎同题，`mid`（2 家共同命中）是常见上限，`high`（3 家同一 URL）较少——各引擎索引差异大，`single` 不等于错，只表示"仅一家报"。
- **数字共识不是正确性证明**：只统计"多少家提到了这个数字"，多家集体引用同一错误源时同样会显示高共识，关键数据仍需回官方源核对。
- 广告过滤的精确率优先设计：权威站（gov.cn/edu.cn/chsi.com.cn/cqksy.cn 等）直接豁免——实测教训是阳光高考那篇《重庆：高考冲刺班1小时400元 老师称或适得其反》含"冲刺班/一对一/400元"，纯关键词法必误杀；含"记者/报道/称/提醒"等新闻性表述且无联系方式时也不判（但"包过/保录取"类违规承诺不受此软化保护）。

## 文搜视频（t~v）实测（2026-09-23）

| 通道 | 状态 | 说明 |
|------|------|------|
| Bing 视频 | ✅ 可用 | `video_search_text.py`：容器 `.mc_vtvc` + `mmeta` JSON（murl 视频真实 URL / turl 缩略图）+ `aria-label` 元信息（标题/来源站/上传人/上传时间）。实测 6/6，来源 douyin.com 为主（可直接播放链接） |
| 百度视频 / 搜狗视频 / B站搜索 | ⬜ 待探 | B站 search 需 wbi 签名（未实现，替代：Bing 视频含 B站源或 card/view 直连） |
| 视频内容理解 | ⬜ 未证实 | 本 skill 已明确不做视频理解（用户否决并入，要求另建 skill）。⚠️ 更正：先前"智谱 marketplace 有视频理解 MCP"的记录系**误读 5.png**（那张图是**图像搜索 MCP**）——智谱市场是否真有视频理解服务**未经证实**，别据此规划 |
