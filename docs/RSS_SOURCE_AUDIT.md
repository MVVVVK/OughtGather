# OughtGather RSS 来源及候选池审计（第一阶段）

审计日期：2026-11-24（北京时间）。基线：`main` 的 `096b7afed99db1fae476a6e7037fa504dfb00a2f`，包含已合并 PR #7。本文件仅为只读审计和待确认方案，不更改配置或运行行为。

## 证据范围与局限

GitHub Actions 从私有 `CONFIG_JSON` 恢复生产配置；未读取或猜测该 Secret。仓库里的 `config.json`、`config.template.json` 是公开示例，不能作为生产来源清单。当前未发现 `Daily_News_Final.opml` 或可供分析的脱敏生产来源快照；过去用户报告的 237 个候选、200 个入 AI、37 个被 cap 排除是一次运行的总数，并非按来源统计。

本次核对了代码、出版商公开目录及条款，也尝试只读访问公开 RSS：执行环境的外网代理对直接 HTTPS 返回 CONNECT 403；网页检索工具对 XML 返回自身的 `400 Unsupported content-type`，而对部分地址受 robots 限制。这个 400 **不是出版商的 HTTP 400**，不代表源失效。未能下载并解析任意 RSS XML，所以表中所有“最近文章日期、有效条数、摘要长度、实时正文质量及重合百分比”均未实测，不能判定稳定可用。目录列出某 URL、检索器认出 XML 类型、第三方目录称其有更新，均不等于本次 XML/实时订阅验证成功。个别文章网页也未实测，因此全文标为待验证或受已知访问限制；不得以绕过访问限制的方式测试。来源之间的“可能重合”是栏目关系推断，不是实测数量。

## 候选 RSS 总表

状态统一为“待验证”：这些不是已加入生产的清单。最近发布时间、条数及摘要字符数：**全部未知**，需在可访问网络中实测并将抓取时间、feed 更新时间和最新文章发布时间分别记录。`XML 类型`表示检索工具报告了 XML MIME，既不证明内容有效，也不证明更新。

| 媒体 / 栏目 | 语言 | 候选 RSS 或目录 | 来源类型及当前证据 | 内容 / 正文 / 处置建议 |
|---|---|---|---|---|
| AP Top Stories | 英 | https://flipboard.com/@associatedpress/top-stories-u7govdf1z.rss | 第三方托管的 AP 账号；[Feeder](https://feeder.co/knowledge-base/rss-content/ap-news-rss-feeds/) 于 2026-11-10 报告其为 AP 运营；本次不可访问 | 待检验是否文字为主、摘要和正文；Top Stories 优先候选，核对 Flipboard 与 AP 条款 |
| AP International | 英 | https://flipboard.com/@associatedpress/international-news-miluvhmaz.rss | 同上，第三方目录所列；未检验 | 可能有国际新闻增量，但与 Top Stories 的重合度未知；先比较再决定 |
| AP 旧 apnews RSS | 英 | https://apnews.com/hub/ap-top-news.rss | 旧官网入口；Feeder 称已停止 | 建议放弃旧入口，除非重新获得官方可用性证据；不可尝试绕过机器人验证 |
| 新华英文 World | 英 | https://www.xinhuanet.com/english/rss/worldrss.xml | 官网上历史目录；本次工具识别 text/xml，内容和时效不明 | 待确认并非过期条目；不能据 MIME 宣称可用；正文和条款待查 |
| 新华英文新地址候选 | 英 | https://english.news.cn/rss/world.xml | 第三方公开配置所列，官网目录未证实 | 待人工核验所有权、RSS 内容、时效及条款；不能当作已验证新源 |
| 新华中文 | 中 | https://www.news.cn/ | 官网栏目；当前未发现可确认持续更新的官方中文 RSS URL | 不凭空指定 feed；中文 RSS 或合法第三方聚合须另行核验 |
| AFP 机构新闻 | 英 | https://www.afp.com/en/actus/afp_communique/all/feed | 官网[机构新闻页](https://www.afp.com/en/agency/inside-afp/press-releases)链接，工具识别 XML | 内容为 AFP 自身活动、合作和公告，**不是每日通讯社国际稿**；不纳入日报 |
| AFP 国际新闻供稿 | 英 / 法 | https://www.afp.com/ | 通讯社专业供稿，取得许可及价格待确认 | 未找到已验证的免费、稳定、可授权使用的原始国际稿 RSS；不要将客户供稿当免费公开 RSS |
| Le Monde Une | 法 | https://www.lemonde.fr/rss/une.xml | [官方目录](https://www.lemonde.fr/le-monde-et-vous/article/2025/07/14/les-flux-rss-du-monde-fr_5498778_3237.html)；工具识别 XML | 优先主源；该目录明确 RSS 为标题、摘要、链接；正文可能受订阅或 JS 限制，不绕过 |
| Le Monde International | 法 | https://www.lemonde.fr/international/rss_full.xml | 官方目录；工具识别 XML | 国际增量优先比较，若证据支持可作为少量补充；与 Une 重合未量化 |
| Le Monde Justice | 法 | https://www.lemonde.fr/justice/rss_full.xml | 官方目录；XML 未解析 | 暂不默认增设；国际相关与独立增量待测，避免国内案件挤占 |
| Le Monde Économie | 法 | https://www.lemonde.fr/economie/rss_full.xml | 官方目录；工具识别 XML | 与 Une/International 比较经济报道的独立增量后再决定 |
| Le Monde Les Décodeurs | 法 | https://www.lemonde.fr/les-decodeurs/rss_full.xml | 官方目录，未解析 | 深度解释可能有价值但非实时通讯；先评估增量及全文可获取性 |
| Washington Post World | 英 | https://www.washingtonpost.com/arcio/rss/category/world/ | [官方 RSS 目录](https://www.washingtonpost.com/information/2026/05/21/washington-post-rss-feeds/)链接；工具识别 XML | 条款未厘清前暂不接入当前 AI/归档流水线，见下文 |
| Washington Post Politics | 英 | https://www.washingtonpost.com/arcio/rss/category/politics/ | 官方目录；工具识别 XML | 与 World 的重合和增量未知；不默认叠加 |
| Guardian World | 英 | https://www.theguardian.com/world/rss | 第三方目录及工具 XML 类型；尚未解析 | 候选主源；文章与摘要长度、正文及条款需实测 |
| Guardian Business | 英 | https://www.theguardian.com/business/rss | 工具识别 XML | 专业源先测边际信息增量，和 World 事件可能相重合 |
| Guardian Technology | 英 | https://www.theguardian.com/technology/rss | 工具识别 XML | 待测是否产生国际性科技独立增量，不默认叠加 |
| Guardian Science | 英 | https://www.theguardian.com/science/rss | 工具识别 XML | 待比较时效与独立增量，不默认叠加 |
| Guardian Global Development | 英 | https://www.theguardian.com/global-development/rss | 工具识别 XML | 可能补充非洲与发展议题，需证明独立增量及新闻时效 |
| France 24 综合 | 英 / 法 | https://www.france24.com/en/rss / https://www.france24.com/fr/rss | 网站 URL；检索工具受 robots 限制 | 先比较语言版报道与视频比例；语言不同不证明为不同事件 |
| France 24 非洲 / 中东 | 英 / 法 | https://www.france24.com/fr/afrique/rss / https://www.france24.com/en/middle-east/rss | 第三方目录所列；未直接解析 | 仅在有独立增量时补充；严格核实文字内容，避免视频摘要进入 |
| RFI Afrique | 法 | https://www.rfi.fr/fr/afrique/rss | 第三方目录所列；检索工具受 robots 限制 | 可能提供非洲本地报道，需与其综合源及 France 24 比较，不保证实时可用 |
| RFI 综合 | 法 | https://www.rfi.fr/fr/rss | 常见候选地址；未确认 | 待证实独立 feed 及有效性；不要将网页生成的第三方 RSS 当作官方源 |
| NPR World | 英 | https://feeds.npr.org/1004/rss.xml | 第三方目录所列；工具识别 XML | 综合国际报道候选；长度、来源权利及正文待测 |
| Al Jazeera English | 英 | https://www.aljazeera.com/xml/rss/all.xml | 第三方目录所列，正文及 XML 待测 | 综合源候选；应识别视频、直播并检查 RSS 中非文字内容 |
| Deutsche Welle | 英 | https://rss.dw.com/xml/rss-en-all | 第三方目录所列，工具未完成 XML 检验 | 综合候选；可与现有欧洲来源进行重合和独立增量比较 |
| Euronews World | 英 / 法 | [官方 RSS 目录](https://www.euronews.com/widgets) | 官网提供 MRSS 目录；需从目录选具体 URL 并核验 | 视频和通讯社转载比例待核验；暂不臆造地址 |
| African Arguments | 英 | https://africanarguments.org/feed/ | 第三方目录所列，未解析 | 非洲专业补充，需区分报道、评论与长期阅读；该站独立于 BBC 但需逐条核验署名与来源 |
| Middle East Eye | 英 | https://www.middleeasteye.net/rss | 第三方目录所列，未解析 | 中东专业补充；独立报道价值、重合及正文待测 |
| Al-Monitor | 英 | https://www.al-monitor.com/rss | 第三方目录所列，未解析 | 中东专业候选；订阅墙和正文访问待测，不绕过 |
| The Conversation | 英 / 法 | https://theconversation.com/ | 未确认本次可用 RSS URL | 以专家解释与观点为主，适合个人阅读或按用户同意单独补充，不自动充当快讯源 |
| Reporterre | 法 | https://reporterre.net/spip.php?page=backend-simple | [官方帮助](https://faq.reporterre.net/article/16-flux-rss)给出 URL；当前工具请求获 403 | 环境新闻专业候选；先诊断可访问性，既不绕过 403，也不将本环境的 403 推断为全球停用 |
| BBC 全部 | 多语言 | 不列出为候选 | 用户明确排除 | 官方及旗下内容、转载身份均需在运行路径实施过滤；单改模板不足 |

## 五家重点媒体与条款

1. **AP**：Flipboard 官方合作关系可见 [Flipboard 介绍](https://about.flipboard.com/inside-flipboard/this-just-in-associated-press-files-with-flipboard/)，但上表具体账户及最新条目本次无法独立解析；[第三方 Feeder](https://feeder.co/knowledge-base/rss-content/ap-news-rss-feeds/) 称 Top Stories 由 AP 账号管理、旧 AP feeds 已失效。Top Stories 是优先验证对象；Google News 的 AP 搜索 RSS 属第三方聚合，链接/归属及条款不同，仅作为待评估替代，不能冒称官方供稿。AP Media API 不应假设免费。
2. **Xinhua**：历史官网 RSS 仅有 MIME 观察；中文长期有效 RSS 未确定。[新华社产品介绍](https://www.xinhuanet.com/xinhuashe/dwjs.htm)指出专业产品通过 RSS/API 等交付且提供试用，这不证明永久免费，专业服务需询授权。可调查新华社网站授权的个人订阅或明确标记的第三方索引，不把商业接口冒充免费 RSS。
3. **AFP**：官网直接链接到的 RSS 是机构新闻，不能拿来冒充法新社国际稿。AFP 原始通讯需核实供稿授权。由其他出版商合法刊登且经授权使用的 AFP 稿可保留署名与原链接，但订阅方对转载、AI 处理和全文使用的权利不能凭署名推定。
4. **Le Monde**：[官方法语目录](https://www.lemonde.fr/le-monde-et-vous/article/2025/07/14/les-flux-rss-du-monde-fr_5498778_3237.html)明确这些 feed 提供标题、摘要和 URL，并限定严格个人、非专业、非集体使用；其他用途需许可。优先验证 Une，International 是可能的高增量分支；使用外部 AI 处理及存档的具体范围也应确认。`rss_full.xml` 名称不能证明含全文。
5. **Washington Post**：官方目录明确 World/Politics 地址。其 [RSS 条款](https://www.washingtonpost.com/discussions/2021/01/01/rss-terms-service/)对展示文本上限、署名链接、重排及存档有要求；[网站服务条款](https://www.washingtonpost.com/terms-of-service/)还限制自动采集和 AI 工具使用。个人阅读例外、RSS 专项条款及现有自动 EPUB/AI/30 天 WebDAV 留存的适用关系需向权利方确认；在未厘清前，不建议将其 RSS 或正文接入现有自动 AI 和归档。单纯禁用全文抓取并不能自动解决 AI 和归档问题。

## 各媒体分支及重复：待验证假设

建议初选每家一个主源，专业分支只有在比较窗口内提供明显新增独立事件且质量达标时保留；现在无数据支持直接删除真实生产源。

| 媒体 | 主源建议 | 可作为增量验证的分支 | 暂缓 / 风险 |
|---|---|---|---|
| Le Monde | Une | International；Économie/Les Décodeurs 仅在有独立价值时 | Justice 国内专案多的可能性，不能以领域作默认加分；比较 Une 与所有分支 |
| Guardian | World | Global Development、Technology / Science 或 Business，按独立贡献择少量 | 一次全加容易增加同品牌覆盖和重复；World 本身非全领域总源 |
| France 24 | 先比较英法综合并择一 | Afrique、Middle East 按独立文字报道贡献决定 | 同文翻译、视频和直播；不假设不同语言就是不同报道 |
| RFI | 优先验证法语综合 | Afrique 可因非洲现场报道作为候选补充 | 若综合地址无效，Afrique 可作主源；勿假设存在综合 RSS |
| Washington Post | 规则及授权明确后 World | Politics 如有显著全球增量 | AI/存档条款先核对；美国国内政治未必有国际增量 |
| AP | 先验证 Top Stories | International 若明显增加世界新闻 | Flipboard 是第三方托管；重复及版权需验证 |

比较方法：对同一可访问网络、同一采样时刻和连续 7–14 天的各 RSS 原始条目，记录原始/成功条数、发布时间、来源标签、摘要非空/长度、文字媒体类型与内容质量。先以规范化原 URL 做同媒体精确重合，再人工抽样判断跨链接同一事件；报告集合差集、交集、时效与独立合格报道数，并标注动态 feed 留存窗口和取样误差。不可用标题相似或同一领域直接断言同事件；不能把海外转载署名、语种或机构标签当作独立原创。逐条判断需尊重条款，避免公开新闻正文、私有来源列表和私人阅读历史。

## 200 候选采样路径与风险

1. `src/main.py::_fetch_source_task` 从 RSS `fetch_list()` 获取条目，按现有历史去重、重试和标题过滤产生 `new_candidates`；随后先交给 `CandidateCollector.collect`，再执行 `fetch_items` 正文质量审核。候选因此可能包括之后被判视频、正文失败或摘要不足的条目；`media_hint` 只是提示，不等同已通过质量。单阶段回退路径可能没有采集，来源失败也可能无候选。
2. ID 是 `sha256(source_key + '\0' + url)`；同一 URL 来自不同 source_key 不一定合并。`snapshot` 先依 `source` 显示标签分组和排序，同一标签的多个 feed 可能合并为一队；每队按 ID 哈希而非时间排序，队列之间按标签排序轮询，达到 cap 时尾轮也可能偏向标签靠前的来源。主程序先 `snapshot(1000000)`，编辑器再按默认 200 截断；轮询均衡的是 feed/标签，不是独立机构或新闻事件。
3. 发布时间只是字符串，可能为空或格式有异；`published` 不等于 feed 更新时间或实际抓取时间。对可可靠解析的 `published_at` 做来源队列内稳定降序、无效日期安全放后并以现有 ID 作 tie-break 是**第二阶段建议**，会改变 200 条实际候选，需经用户同意、离线回放及极端时区/无日期/未来日期测试。不能改变正式去重或把旧候选永久排除；不能按国家、语言、媒体设置机械选稿配额。
4. 需要按 feed 和媒体测量候选进入/超 cap、最新发布时间、历史过滤、空摘要/质量拒绝、媒体类型、交叉重合和边际独立事件。现有 `quality-sources.json` 有 RSS 返回数、时间、全文/摘要、失败、入选等统计，但不是 AI 200 入围贡献报告；不能从本次汇总反推每家贡献、缺失地区或哪些重大新闻被漏选。获取脱敏汇总后再量化，不需访问 `CONFIG_JSON`。
5. 预计按时间排序有利时效，但可能使各源稳定挤掉部分仍有价值的旧稿；缺失时间的来源可能吃亏。将同机构多 feed 直接合并队列会改变轮询权重，可能损害独立报道；需先量化、设计可测试的回放和回滚方案。

## BBC 排除方案：待确认，尚未实施

建议在通用运行时入口使用代码层策略，覆盖 Secret、模板及手动配置，不读取或更改 Secret：在 RSS/web 来源进入 fetch 阶段按解析后的主机名、来源元数据及已知集团身份做确定性判断，匹配根域和子域时采用规范域名边界，不能用裸字符串子串误杀相似域。抓取候选时再次检查条目真实 URL、来源/作者/署名及明确的 BBC 旗下身份；最终正式 publication 和 Shadow collector 两条路径均需保证排除。对聚合 RSS 的外链、跳转、BBC 转载稿及缺失/伪造归属，设计合法可访问范围内的来源核验、未知身份处理和解释性诊断；不因正文“提到 BBC”就排除非 BBC 新闻。对无法可靠辨别的聚合条目说明漏判风险，不能宣称静态域名表可证明 100% 身份识别。

需离线测试 BBC World/Africa/其它集团域、大小写、子域、参数、相似域、第三方聚合/跳转、BBC 署名转载、非 BBC 稿件提及 BBC、未知身份、RSS 和 web、正式 EPUB、AI Shadow、去重隔离、`test_mode` 与无 Secret 的配置。排除应发生在提交正式出版记录之前，不清空或破坏既有历史；不改变现有缓存格式。对于 BBC 以非机器可识别方式被第三方转载，仍需人工核验。

## 拟议来源分级与后续验证

- **已独立验证当前 XML 有效且更新：无。** 本次工具不支持解析 XML，不将 MIME 识别当作通过。
- **待优先验证**：Le Monde Une 与 International；AP Flipboard Top Stories；Guardian World；France 24 一种综合语言；RFI Afrique；NPR World；Al Jazeera；DW；Middle East Eye；African Arguments；Reporterre 的网络可达性。
- **需版权/AI 使用与归档范围明确后再评估**：Washington Post World、AP/Flipboard、Le Monde 及其他提供者；专业 AFP、新华社供稿必须核准授权与价格。
- **暂缓或建议放弃**：BBC 所有源；AFP 机构活动 feed；旧 AP 源；无证据持续更新的旧 Xinhua RSS；新闻以外的博客、随笔及仅含音视频的源。当前公开模板中的 Hacker News、阮一峰日志、少数派等是示例而非生产清单，不可据此声称它们正在运行。

推荐的第二阶段工作顺序：先确认允许以何种方式获得脱敏运行数据并核对主要源权利；使用经授权、正常可访问的网络完成 7–14 天 feed 数据和源内/源间重复对照；提交具体保留/移除清单及回放结果；用户批准后才实现 BBC 全链路屏蔽和候选时间排序，并对正式 EPUB、RSS 去重、WebDAV、Memory 及 200 cap 做回归测试。任何受限或失败的源只记录状态，不尝试绕过访问控制。

**预期与风险：** 过滤 BBC 后会减少该机构覆盖但不会自动替代其全球报道；按来源时间排序可能提高新鲜度但不能保证事件重要性；精简分支可能减少重复和某媒体的轮询权重，但也可能丢失重要独家报道。若源实际不可访问、条款不适用于自动存档或 AI、摘要稀少、feed 内含视频或发表时间不可靠，实际收益可能低于预期。
