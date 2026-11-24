# RSS 实测与候选池复核：2026-11-24

> **第三阶段更新：** 两次公开快照、匿名运行 #31 统计和最新拟议清单见 [第三阶段审计报告](RSS_PHASE3_AUDIT.md)。本文保留先前阶段的时间范围及数据缺口。

本报告基于 [GitHub Actions run 45244541615](https://github.com/MVVVVK/OughtGather/actions/runs/45244541615) 的只读公开探测；时间统一列为北京时间（UTC+8）。完整的哈希级元数据在该运行的 `rss-public-diagnostic` Artifact（计划于 2026-12-01 03:02 北京时间到期），不包含 RSS 正文、摘要文本、文章标题、私有配置或认证信息。[持久化汇总数据](data/rss_probe_2026-11-24.json)包含每源指标及比较值，不包含单篇正文。

## 文件整合

`Daily_News_EN_FR_3.opml` 有 20 个不同 URL，全部包含在 `Daily_News_Final_2.opml` 的 43 个不同 URL 内。`RSS_Integration_Audit_2.csv` 共 249 行：`news` 43 条、`personal` 206 条；新闻 URL 与 43 条清单完全一致，个人 URL 206 个互不重复，新闻与个人 URL 无精确交集。CSV 里的状态是历史清单标签，不是本次 RSS 实测结果。个人订阅没有加入公开探测或生产配置。本次仅提交计数，不公开个人清单。

## 测量范围和限制

单次运行共测 49 个公开 RSS 候选（43 条新闻清单 + 6 个重点媒体候选）；41 个解析出 RSS/Atom（其中 36 个来自 43 条清单）。其余：3 个因 robots 请求返回 403/301 而未请求 feed、1 个 feed 返回 301 且未跟随、1 个 HTTP 200 但非 RSS/Atom、2 个超过 2 MiB 上限、1 个超时。失败只适用于此次 GitHub Runner，不推断网站永久失效。若 robots 不可用、访问受限或跳转，诊断保守停止，不绕过；未发送 Secrets、未使用 AI，未抓取文章网页正文。

“有效条目”仅表示 RSS 含非空标题和 HTTP(S) 链接，**不是人工核实的独立新闻或可读全文**。媒体标记为标题、标签和路径启发式提示，非人工确认的视频/文字比例；未标记的条目也可能是音视频、直播、观点或公告。平均摘要长度是 RSS 摘要 HTML 清洗后的字符数，不代表全文质量或授权。RSS feed 更新时间、新闻发表时间和实际单源抓取时间分开记录；Atom/RSS 只有更新日期时，不将其冒充发表时间。单次快照不能保证长期可用；待复测有 403、301、大小限制和超时的项目。

## 全部来源的脱敏实测表

最新发表为 `—` 表示无可解析发表时间，不是零篇。`摘要%` 为有效条目中摘要非空比例；`媒体提示%` 为有效条目中媒体提示比例，不是已核实视频比例。每源实际抓取时刻及 feed 更新时间见 JSON。

| 来源 | RSS | 有效条目 | 近 7 天 | 最新发表（北京） | 摘要非空 | 平均摘要字数 | 媒体提示 | 本次状态 |
|---|---|---:|---:|---|---:|---:|---:|---|
| BBC World | https://feeds.bbci.co.uk/news/world/rss.xml | 32 | 32 | 2026-11-24 02:04 | 100.0% | 113.8 | 9.4% | ok (HTTP 200) |
| The Guardian World | https://www.theguardian.com/world/rss | 45 | 45 | 2026-11-24 02:51 | 100.0% | 667.2 | 11.1% | ok (HTTP 200) |
| NPR News | https://feeds.npr.org/1001/rss.xml | 10 | 10 | 2026-11-24 00:00 | 100.0% | 178.8 | 0.0% | ok (HTTP 200) |
| Al Jazeera English | https://www.aljazeera.com/xml/rss/all.xml | 25 | 25 | 2026-11-24 02:25 | 100.0% | 105 | 44.0% | ok (HTTP 200) |
| France 24 English | https://www.france24.com/en/rss | 24 | 24 | 2026-11-24 01:52 | 91.7% | 335.1 | 50.0% | ok (HTTP 200) |
| Deutsche Welle English | https://rss.dw.com/rdf/rss-en-all | 139 | 0 | — | 100.0% | 189.1 | 8.6% | ok (HTTP 200) |
| Euronews English | https://www.euronews.com/rss | 50 | 50 | 2026-11-24 02:42 | 100.0% | 187.5 | 10.0% | ok (HTTP 200) |
| ABC News Australia — Just In | https://www.abc.net.au/news/feed/51120/rss.xml | 25 | 25 | 2026-11-24 02:39 | 100.0% | 149.9 | 0.0% | ok (HTTP 200) |
| PBS NewsHour | https://www.pbs.org/newshour/feeds/rss/headlines | — | — | — | — | — | — | robots_unavailable (robots 403) |
| ProPublica — Articles | https://www.propublica.org/feeds/propublica/main | 20 | 18 | 2026-11-23 19:00 | 100.0% | 126.8 | 0.0% | ok (HTTP 200) |
| Africanews — English | https://africanews.com/feed/rss | — | — | — | — | — | — | robots_unavailable (robots 301) |
| BBC News — Africa | https://feeds.bbci.co.uk/news/world/africa/rss.xml | 29 | 17 | 2026-11-23 23:41 | 100.0% | 123.6 | 24.1% | ok (HTTP 200) |
| France 24 — Africa (EN) | https://www.france24.com/en/africa/rss | 30 | 14 | 2026-11-23 22:36 | 100.0% | 364.1 | 23.3% | ok (HTTP 200) |
| African Arguments | https://africanarguments.org/feed/ | 10 | 4 | 2026-11-23 08:08 | 100.0% | 734 | 0.0% | ok (HTTP 200) |
| Middle East Eye | https://www.middleeasteye.net/rss | 20 | 20 | 2026-11-24 02:46 | 100.0% | 3239 | 55.0% | ok (HTTP 200) |
| Al-Monitor | https://www.al-monitor.com/rss | 20 | 20 | 2026-11-24 01:32 | 100.0% | 204.3 | 0.0% | ok (HTTP 200) |
| The New Arab | https://www.newarab.com/rss | 82 | 82 | 2026-11-24 01:49 | 100.0% | 144.6 | 1.2% | ok (HTTP 200) |
| POLITICO Europe | https://www.politico.eu/feed/ | 10 | 10 | 2026-11-24 02:00 | 100.0% | 144.3 | 10.0% | ok (HTTP 200) |
| The Guardian Business | https://www.theguardian.com/uk/business/rss | 40 | 40 | 2026-11-24 02:46 | 100.0% | 657.8 | 12.5% | ok (HTTP 200) |
| The Guardian Technology | https://www.theguardian.com/uk/technology/rss | 31 | 24 | 2026-11-24 02:51 | 100.0% | 756.9 | 0.0% | ok (HTTP 200) |
| The Guardian Science | https://www.theguardian.com/science/rss | 27 | 22 | 2026-11-24 02:22 | 100.0% | 637.5 | 25.9% | ok (HTTP 200) |
| The Conversation Global | https://theconversation.com/global/articles.atom | 45 | 14 | 2026-11-23 10:56 | 100.0% | 117.2 | 2.2% | ok (HTTP 200) |
| Vox | https://www.vox.com/rss/index.xml | 10 | 10 | 2026-11-24 02:40 | 100.0% | 330.1 | 10.0% | ok (HTTP 200) |
| Project Syndicate | https://www.project-syndicate.org/rss | 20 | 20 | 2026-11-24 01:11 | 100.0% | 298.6 | 0.0% | ok (HTTP 200) |
| The Atlantic | https://www.theatlantic.com/feed/all/ | 25 | 25 | 2026-11-24 02:02 | 100.0% | 78.8 | 4.0% | ok (HTTP 200) |
| The Guardian — International Development | https://www.theguardian.com/global-development/rss | 45 | 12 | 2026-11-23 14:00 | 100.0% | 730.4 | 6.7% | ok (HTTP 200) |
| France 24 Français | https://www.france24.com/fr/rss | 24 | 24 | 2026-11-24 02:35 | 91.7% | 304.2 | 0.0% | ok (HTTP 200) |
| RFI Français | https://www.rfi.fr/fr/rss | 23 | 23 | 2026-11-24 02:19 | 100.0% | 425.5 | 4.3% | ok (HTTP 200) |
| Le Monde — Une | https://www.lemonde.fr/rss/une.xml | 16 | 16 | 2026-11-24 01:43 | 100.0% | 255.7 | 6.2% | ok (HTTP 200) |
| TV5MONDE Info | https://information.tv5monde.com/rss.xml | — | — | — | — | — | — | redirect_not_followed (HTTP 301) |
| Le Monde — International | https://www.lemonde.fr/international/rss_full.xml | 20 | 20 | 2026-11-24 02:31 | 100.0% | 232.2 | 15.0% | ok (HTTP 200) |
| The Conversation France | https://theconversation.com/fr/articles.atom | 50 | 50 | 2026-11-24 02:40 | 100.0% | 186 | 2.0% | ok (HTTP 200) |
| Euronews — Monde (FR) | https://fr.euronews.com/rss?format=mrss&level=theme&name=news | 50 | 50 | 2026-11-24 02:03 | 100.0% | 207.8 | 58.0% | ok (HTTP 200) |
| Reporterre — Écologie et société | https://reporterre.net/spip.php?page=backend-simple | — | — | — | — | — | — | robots_unavailable (robots 403) |
| Africanews — Français | https://fr.africanews.com/feed/rss | 50 | 50 | 2026-11-24 01:05 | 100.0% | 206 | 2.0% | ok (HTTP 200) |
| RFI — Afrique | https://www.rfi.fr/fr/afrique/rss | 24 | 24 | 2026-11-24 01:48 | 95.8% | 437.4 | 4.2% | ok (HTTP 200) |
| France 24 — Moyen-Orient | https://www.france24.com/fr/moyen-orient/rss | 30 | 15 | 2026-11-23 23:53 | 100.0% | 349.1 | 3.3% | ok (HTTP 200) |
| Le Monde — Justice | https://www.lemonde.fr/justice/rss_full.xml | 20 | 20 | 2026-11-24 00:37 | 100.0% | 232.2 | 0.0% | ok (HTTP 200) |
| Le Monde — Économie | https://www.lemonde.fr/economie/rss_full.xml | 20 | 20 | 2026-11-24 02:13 | 100.0% | 249.3 | 10.0% | ok (HTTP 200) |
| Le Monde — Les Décodeurs | https://www.lemonde.fr/les-decodeurs/rss_full.xml | 20 | 18 | 2026-11-22 21:00 | 100.0% | 260.6 | 5.0% | ok (HTTP 200) |
| 36氪 | https://36kr.com/feed | — | — | — | — | — | — | not_feed (HTTP 200) |
| 数据解读 | https://www.stats.gov.cn/sj/sjjd/rss.xml | — | — | — | — | — | — | size_limit (HTTP 200) |
| 数据发布 | https://www.stats.gov.cn/sj/zxfb/rss.xml | — | — | — | — | — | — | size_limit (HTTP 200) |
| AP Top Stories via Flipboard | https://flipboard.com/@associatedpress/top-stories-u7govdf1z.rss | 29 | 29 | 2026-11-23 22:51 | 100.0% | 298.8 | 3.5% | ok (HTTP 200) |
| Xinhua English World legacy | https://www.xinhuanet.com/english/rss/worldrss.xml | 20 | 0 | 2018-01-24 08:00 | 100.0% | 179.8 | 0.0% | ok (HTTP 200) |
| Xinhua via AnyFeeder | https://plink.anyfeeder.com/newscn/whxw | — | — | — | — | — | — | timeout |
| AFP agency press releases (not news) | https://www.afp.com/en/actus/afp_communique/all/feed | 10 | 0 | 2026-11-10 17:34 | 100.0% | 206.6 | 20.0% | ok (HTTP 200) |
| Washington Post World | https://www.washingtonpost.com/arcio/rss/category/world/ | 12 | 12 | 2026-11-24 01:30 | 100.0% | 147.2 | 0.0% | ok (HTTP 200) |
| Washington Post Politics | https://www.washingtonpost.com/arcio/rss/category/politics/ | 18 | 18 | 2026-11-24 01:57 | 100.0% | 135 | 0.0% | ok (HTTP 200) |

## 同窗口栏目比较（近七天）

主源和分支在同一轮运行中请求，随后只统计从各 feed 当前返回的条目中发表时间位于共同 7 日窗口的部分。RSS 常只保留最新 10–50 条，这**不是完整七天出版记录**。相同数仅统计 URL 规范化一致或标题规范化完全一致；跨语言、改写标题、不同 URL 的同一事件未被判重。未匹配数只是供人工复查的上界，**不证明对应数量的独立新闻增量**。不能根据本表机械删除栏目。

| 主源 | 专业栏目 | 主源条目 | 栏目条目 | 相同 URL/标题 | 未匹配（独立性未证实） |
|---|---|---:|---:|---:|---:|
| Le Monde — Une | Le Monde — International | 16 | 20 | 6 | 14 |
| Le Monde — Une | Le Monde — Justice | 16 | 20 | 1 | 19 |
| Le Monde — Une | Le Monde — Économie | 16 | 20 | 5 | 15 |
| Le Monde — Une | Le Monde — Les Décodeurs | 16 | 18 | 0 | 18 |
| The Guardian World | The Guardian Business | 45 | 40 | 2 | 38 |
| The Guardian World | The Guardian Technology | 45 | 24 | 2 | 22 |
| The Guardian World | The Guardian Science | 45 | 22 | 0 | 22 |
| The Guardian World | The Guardian — International Development | 45 | 12 | 3 | 9 |
| France 24 English | France 24 — Africa (EN) | 24 | 14 | 1 | 13 |
| France 24 Français | France 24 — Moyen-Orient | 24 | 15 | 1 | 14 |
| RFI Français | RFI — Afrique | 23 | 24 | 5 | 19 |

需要在真实网络可达并符合条款的条件下，进行多天快照和人工事件级抽样，特别核对英法版本和不同报道角度；不能把 URL 不同误当作新闻独立。由于此轮有意不保存标题正文，当前无法直接对哈希不相同的报道进行人工事件复核，独立增量尚未通过验收。

## 五家重点媒体和 BBC

- **BBC World**：本轮 HTTP 200，32 条标题与链接有效，32 条有摘要、均在近七天；符合当前阶段“实测 RSS 可用”的条件。仍需观察长期稳定性及许可。BBC Africa 也解析出 RSS，但按用户偏好建议从实际运行配置删除专业分支，World 不给予配额或额外优先级。生产配置尚未查看或修改。
- **AP**：Flipboard 上 AP Top Stories 本轮 29 条标题/链接有效，摘要均非空，属于第三方托管的官方账号线索；需要核实持续更新、站点条款和完整正文的许可，并不等同 AP 官方商业接口授权。
- **新华社**：官网旧英文 World XML 的最新发表为 2018-01-24，不应因 200 和 XML 格式而视为当前新闻；AnyFeeder 第一次有解析结果、第二次超时，是第三方源，稳定性、署名归属和授权均待验证。
- **AFP**：本次机构 feed 虽解析成功，但它属于机构公告，不是通讯社国际新闻稿。不能以此替代有授权的 AFP 供稿。
- **Le Monde**：Une 和 International 均解析成功，摘要非空；Une 16 条、International 20 条，当前快照相同 URL/标题 6 条。International 尚有 14 条未匹配，但不是 14 个已证明独立事件。可优先提名 Une，International 作为待进一步核查的高价值分支；遵循[官方 RSS 条款](https://www.lemonde.fr/le-monde-et-vous/article/2025/07/14/les-flux-rss-du-monde-fr_5498778_3237.html)，不绕过访问限制。
- **Washington Post**：官方目录的 World 和 Politics 本次分别解析 12、18 条，摘要非空；其 [RSS 条款](https://www.washingtonpost.com/discussions/2021/01/01/rss-terms-service/)及[网站条款](https://www.washingtonpost.com/terms-of-service/)对存档、重排、自动采集及 AI 有限制。由于现有流程含 AI 和 WebDAV 保存，适用范围或授权未厘清前，不建议启用自动接入；RSS HTTP 200 不等于许可全文或 AI 使用。

## 200 篇候选与质量：代码证据、未知值和方案

`src/main.py::_fetch_source_task` 对历史去重后的 `new_candidates` 调用 `CandidateCollector.collect`，**早于** `fetch_items` 的正文质量检查。`media_hint` 保存在候选快照，但 `src/ai/editor.py::run` 在构建模型请求时只取标题、来源、语言、发表日期和 RSS 摘要。之后按来源标签轮询、每个来源内部按 URL 与来源 key 计算的 SHA-256 ID 排序，再由编辑器截取默认前 200 个。因此，不合格条目、媒体候选、后续访问失败者**可以**占用 200 个 AI 名额；这是确定的代码路径风险，不是已经证明它们在本次生产运行占了多少名额。正式抓取配额跳过的文章也可能是未验证而不是劣质，不应一律排除。

此前用户提供的生产运行总计 237 收集、200 入 AI、37 超出 cap；没有脱敏的**逐源**候选和质量统计，不能据此计算各来源贡献、被挤掉的有效新闻、质量失败条目占比。原始质量 Artifact 可能包含生产源 URL；本次未读取或发布私有配置。此轮 49 条是公开历史候选，不是生产来源清单。

建议获得经用户核准的脱敏运行数据：稳定匿名 source ID、原始数/历史去重数/可尝试数/进入 200 数/未入数、独立文字媒体提示、已尝试的 `full_text`/`rss_content`/`summary`/`rejected`/`failed`，以及区分 `quota_limit` 等“未尝试”和明确质量失败；不提供来源 URL、凭据、正文或阅读历史。只有将这些统计按相同运行/匿名 source ID 对齐，才能计算来源占比和挤占影响。

在用户批准后可先做离线回放：保留来源轮询和确定性；源内对可靠发表时间降序并使用稳定 ID 作 tie-break，对缺失、非法、明显未来时间设明确策略，**不能把 feed 更新或条目更新默认为发表时间**；本次 DW 139 条缺少可解析发表时间，若简单将缺失时间全部压后，会系统性损害该源。对标题链接相同可做高置信精确去重，跨媒体同事件应留人工或现有 Topic-First 判断，不机械按相同学科合并。可识别的纯媒体和已明确质量失败的候选在进入 AI 前单独处理，但保留未尝试和合法摘要候选；必须避免改变正式 RSS/EPUB、publication dedup 和 WebDAV。任何实际实现均待用户审核。

## 拟议清单：仅供审核

| 处理 | 提议 | 证据与限制 |
|---|---|---|
| 拟保留 | BBC World；Le Monde Une；Guardian World；France 24 与 RFI 综合源中的合适语言版本 | 该次可解析且有近期条目；语言版、权利和正文访问仍须评估。BBC 不加权。 |
| 建议移除 | BBC Africa 及其他 BBC 专业分支；AFP 机构公告当作新闻源的用法；停更的 Xinhua 旧英文 feed | BBC 由用户偏好决定，非内容质量判断；AFP 是公告，新华旧 feed 最新发表 2018 年。实际生产中是否存在仍未知。 |
| 候选补充 | AP Flipboard Top Stories；新华社第三方候选；Le Monde International；RFI Afrique；Guardian 专业栏目 | 已有 RSS/单次数据或潜在线索；跨 feed 的事件独立增量、持续稳定性与许可未完成核实，不立即替换生产。 |
| 授权后再议 | Washington Post World | 栏目可解析，但自动 AI、存档和抓取使用条款需要明确；用户偏好不转成机械选稿加分。 |
| 暂不作决定 | 43 项中其余源、其它非 BBC 专业栏目、个人阅读与评论源 | 数据不足、内容类型不同或正文权利待审；保留个人阅读库与日报候选的边界。 |

本报告不批准或执行生产来源删除替换；下一步需用户审核、明确许可并提供脱敏生产统计或独立可信的来源级摘要。诊断只访问上面显式列出的公开 URL，不使用私有 CONFIG_JSON，也不调用 DeepSeek。
