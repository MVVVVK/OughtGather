# OughtGather RSS 来源及候选池审计（第一阶段）

审计日期：2026-11-24（北京时间）。基线：`main` 的 `096b7afed99db1fae476a6e7037fa504dfb00a2f`，包含已合并 PR #7。本文件仅为只读审计和待确认方案，不更改配置或运行行为。

## 证据范围与局限

GitHub Actions 从私有 `CONFIG_JSON` 恢复生产配置；未读取或猜测该 Secret。仓库里的 `config.json`、`config.template.json` 是公开示例，不能作为生产来源清单。已收到并解析 `Daily_News_Final_2.opml`（43 条、43 个不同 URL）；未收到另外的 `Daily_News_Final.opml`、`Daily_News_EN_FR.opml` 或 `RSS_Integration_Audit.csv`，无法判断前者是否与本附件完全相同，也无法审计后两份文件。工作区 `output/quality-sources.json` 含两条本地测试主机记录、无成功 RSS；`quality-report.json` 为空，不能作为生产运行证据。过去用户报告的 237 个候选、200 个入 AI、37 个被 cap 排除是一次运行的总数，并非按来源统计。

本次核对了代码、出版商公开目录及条款，也尝试只读访问公开 RSS：执行环境的外网代理对直接 HTTPS 返回 CONNECT 403；网页检索工具对 XML 返回自身的 `400 Unsupported content-type`，而对部分地址受 robots 限制。这个 400 **不是出版商的 HTTP 400**，不代表源失效。未能下载并解析任意 RSS XML，所以表中所有“最近文章日期、有效条数、摘要长度、实时正文质量及重合百分比”均未实测，不能判定稳定可用。目录列出某 URL、检索器认出 XML 类型、第三方目录称其有更新，均不等于本次 XML/实时订阅验证成功。个别文章网页也未实测，因此全文标为待验证或受已知访问限制；不得以绕过访问限制的方式测试。来源之间的“可能重合”是栏目关系推断，不是实测数量。

## 本次上传 OPML：43 条完整来源清单

附件 `Daily_News_Final_2.opml` 确认为 XML 格式的 OPML；按 `xmlUrl` 提取 43 条、精确去重 43 条。对 URL 的 scheme/host 大小写和末尾斜杠做保守归一化后仍是 43 条；没有推断不同地址一定属于不同内容。原始 9 个分类原样保留如下。所有来源的发布时间、实际条目数、RSS 摘要长度、文章正文质量、确切机构内或跨机构事件重合率及其是否在生产配置中，当前均**未知**。

本次逐一用公开检索工具打开 43 个 URL：25 个提示“不支持 XML 内容类型”，6 个返回 403，9 个工具标记为不可访问，1 个超时，1 个受工具限制，1 个收到 XHTML 类型。这里的 XML 类型只说明检索器识别出内容类型，不能证明是有效 RSS、HTTP 状态成功或实时更新；403 是本次检索访问结果，不足以判断所有网络均不可用。直接命令行和额外网络权限尝试均被网络代理 CONNECT 403 阻止；不绕过网站或代理限制。**已验证有效 XML = 0；可用于生产须在用户允许的正常网络中复测。**

| 原 OPML 分类 | 来源 | 完整 RSS URL | 本次探测 | 审核前建议 |
|---|---|---|---|---|
| 01 · English — World News | BBC World | https://feeds.bbci.co.uk/news/world/rss.xml | XML 类型；未解析 | 拟保留综合源；先实测 |
| 01 · English — World News | The Guardian World | https://www.theguardian.com/world/rss | XML 类型；未解析 | 拟保留综合源；先实测 |
| 01 · English — World News | NPR News | https://feeds.npr.org/1001/rss.xml | XML 类型；未解析 | 候选综合源；先实测 |
| 01 · English — World News | Al Jazeera English | https://www.aljazeera.com/xml/rss/all.xml | XML 类型；未解析 | 候选综合源；先实测并筛视频 |
| 01 · English — World News | France 24 English | https://www.france24.com/en/rss | 访问返回 403 | 英法综合择优比较，先实测 |
| 01 · English — World News | Deutsche Welle English | https://rss.dw.com/rdf/rss-en-all | XML 类型；未解析 | 候选综合源；先实测 |
| 01 · English — World News | Euronews English | https://www.euronews.com/rss | XML 类型；未解析 | 候选综合源；先实测 |
| 01 · English — World News | ABC News Australia — Just In | https://www.abc.net.au/news/feed/51120/rss.xml | XML 类型；未解析 | 候选综合源；先实测国际占比 |
| 01 · English — World News | PBS NewsHour | https://www.pbs.org/newshour/feeds/rss/headlines | 访问返回 403 | 候选综合源；核实时效及可达 |
| 01 · English — World News | ProPublica — Articles | https://www.propublica.org/feeds/propublica/main | XML 类型；未解析 | 调查报道为主；暂放独立深读候选 |
| 02 · English — Africa | Africanews — English | https://africanews.com/feed/rss | 检索工具不可访问 | 地区候选；检查英法重合 |
| 02 · English — Africa | BBC News — Africa | https://feeds.bbci.co.uk/news/world/africa/rss.xml | XML 类型；未解析 | 建议删除专业分支；保留 World 待测 |
| 02 · English — Africa | France 24 — Africa (EN) | https://www.france24.com/en/africa/rss | 检索工具不可访问 | 只有独立增量成立才保留 |
| 02 · English — Africa | African Arguments | https://africanarguments.org/feed/ | XML 类型；未解析 | 地区候选；区分报道与评论 |
| 03 · English — Middle East & MENA | Middle East Eye | https://www.middleeasteye.net/rss | 请求超时 | 地区候选；超时待复测 |
| 03 · English — Middle East & MENA | Al-Monitor | https://www.al-monitor.com/rss | XML 类型；未解析 | 地区候选；核验授权/付费墙 |
| 03 · English — Middle East & MENA | The New Arab | https://www.newarab.com/rss | XML 类型；未解析 | 地区候选；核验独立增量 |
| 04 · English — Politics, Economy & Society | POLITICO Europe | https://www.politico.eu/feed/ | 检索工具限制 | 欧洲专业候选；待测 |
| 04 · English — Politics, Economy & Society | The Guardian Business | https://www.theguardian.com/uk/business/rss | XML 类型；未解析 | 有独立经济增量再保留 |
| 04 · English — Politics, Economy & Society | The Guardian Technology | https://www.theguardian.com/uk/technology/rss | XML 类型；未解析 | 有独立科技增量再保留 |
| 04 · English — Politics, Economy & Society | The Guardian Science | https://www.theguardian.com/science/rss | XML 类型；未解析 | 有独立科学增量再保留 |
| 04 · English — Politics, Economy & Society | The Conversation Global | https://theconversation.com/global/articles.atom | 检索工具不可访问 | 解释性文章；单列个人深读候选 |
| 04 · English — Politics, Economy & Society | Vox | https://www.vox.com/rss/index.xml | 检索工具不可访问 | 观点/解释为主；单列深读候选 |
| 04 · English — Politics, Economy & Society | Project Syndicate | https://www.project-syndicate.org/rss | XML 类型；未解析 | 评论为主；单列深读候选 |
| 04 · English — Politics, Economy & Society | The Atlantic | https://www.theatlantic.com/feed/all/ | XML 类型；未解析 | 杂志深读；不自动入快讯池 |
| 04 · English — Politics, Economy & Society | The Guardian — International Development | https://www.theguardian.com/global-development/rss | XML 类型；未解析 | 有独立发展议题增量再保留 |
| 05 · Français — Actualités générales | France 24 Français | https://www.france24.com/fr/rss | 访问返回 403 | 英法综合择优比较，先实测 |
| 05 · Français — Actualités générales | RFI Français | https://www.rfi.fr/fr/rss | 检索工具不可访问 | 综合源有效性待确认 |
| 05 · Français — Actualités générales | Le Monde — Une | https://www.lemonde.fr/rss/une.xml | XML 类型；未解析 | 拟保留法语综合主源；先实测 |
| 05 · Français — Actualités générales | TV5MONDE Info | https://information.tv5monde.com/rss.xml | 访问返回 403 | 综合候选；检查可达与视频比例 |
| 05 · Français — Actualités générales | Le Monde — International | https://www.lemonde.fr/international/rss_full.xml | XML 类型；未解析 | 国际增量确认后可保留 |
| 05 · Français — Actualités générales | The Conversation France | https://theconversation.com/fr/articles.atom | 检索工具不可访问 | 解释性文章；单列个人深读候选 |
| 05 · Français — Actualités générales | Euronews — Monde (FR) | https://fr.euronews.com/rss?format=mrss&level=theme&name=news | XML 类型；未解析 | 同机构英法比较后择优 |
| 05 · Français — Actualités générales | Reporterre — Écologie et société | https://reporterre.net/spip.php?page=backend-simple | 访问返回 403 | 环境专业候选；访问与增量待核 |
| 06 · Français — Afrique | Africanews — Français | https://fr.africanews.com/feed/rss | XHTML 类型，非 RSS 证据 | 非 RSS 内容迹象；先核验合法地址 |
| 06 · Français — Afrique | RFI — Afrique | https://www.rfi.fr/fr/afrique/rss | 访问返回 403 | 非洲增量确认后可保留 |
| 07 · Français — Moyen-Orient | France 24 — Moyen-Orient | https://www.france24.com/fr/moyen-orient/rss | 检索工具不可访问 | 中东增量确认后可保留 |
| 08 · Français — Politique, économie & droit | Le Monde — Justice | https://www.lemonde.fr/justice/rss_full.xml | 检索工具不可访问 | 国际增量不明，建议暂缓 |
| 08 · Français — Politique, économie & droit | Le Monde — Économie | https://www.lemonde.fr/economie/rss_full.xml | XML 类型；未解析 | 经济增量确认后可保留 |
| 08 · Français — Politique, économie & droit | Le Monde — Les Décodeurs | https://www.lemonde.fr/les-decodeurs/rss_full.xml | XML 类型；未解析 | 解释报道；独立增量确认后再定 |
| 09 · 中文 — 东亚与经济 | 36氪 | https://36kr.com/feed | 检索工具不可访问 | 商业科技源，非综合世界主源 |
| 09 · 中文 — 东亚与经济 | 数据解读 | https://www.stats.gov.cn/sj/sjjd/rss.xml | XML 类型；未解析 | 官方统计解读，非独立新闻 |
| 09 · 中文 — 东亚与经济 | 数据发布 | https://www.stats.gov.cn/sj/zxfb/rss.xml | XML 类型；未解析 | 官方数据发布，非独立新闻 |

### 实施前需要的证据

- “拟保留/删除”仅针对这份历史 OPML；真实生产配置未见，不能宣称其已添加或删除。网址无重复不代表不同栏目或语言版具有独立新闻增量。
- BBC World 先实测 RSS 内容及时效，通过后保留；BBC Africa 从拟议清单移除，不因两者目前都呈 XML MIME 就认定可用。
- Guardian World 与 Business、Technology、Science、Global Development；Le Monde Une 与 International、Justice、Économie、Les Décodeurs；France 24 英法综合及 Africa/Moyen-Orient；RFI 综合与 Afrique 需要在同一窗口采样后比较交集、独立合格新闻、时效、视频比例、摘要与版权；现阶段没有数据证明任一专业分支有足够边际增量。
- AP、Xinhua、AFP、Washington Post 不在本附件中；上文单独列出的合法接入线索均应先验证。不得通过旧地址、企业公告 feed 或猜测的付费供稿假称取得新闻 RSS。
- 两条国家统计局订阅属于官方数据/解读，36氪侧重商业科技；The Conversation、Vox、Project Syndicate、The Atlantic 以解释、评论或长阅读为潜在主项，不能因为存在于个人 OPML 就自动列作每日世界新闻主源，仍须逐条核实实际内容。
- 工具只读尝试无法验证正文或发布时间；对任何 403、机器人验证、订阅墙和自动访问限制，不进行绕过。适用条款和向外部 AI 发送内容须先核实。

### 用户补充的 RSS 目录与候选线索

- [quanwenrss OPML](https://feed.quanwenrss.com/feed/opml.xml)：当前检索工具不可访问，未获得清单，不能说已核验其来源或更新情况。
- [awesome-rsshub-routes](https://github.com/JackyST0/awesome-rsshub-routes#how-to-use)：目录明确区分网站官方 RSS 和 RSSHub 生成路由，内容兼有科技社区、博客、热点与视频；只能用作候选发现，不能整库导入。第三方 RSSHub 不等于出版商授权或运营。
- [AnyFeeder 常用源](https://plink.anyfeeder.com/)：目录能读取，混有新闻、博客、公众号及其他内容。其“新华网 / 新华社新闻”链接指向 `https://plink.anyfeeder.com/newscn/whxw`，是**第三方生成或聚合候选**，不是新华社官方 RSS；检索工具提示 XML 类型但无法解析，文章归属、更新、版权与合法访问方式均待核实，不直接加入生产。同目录的联合早报、澎湃等亦为第三方地址，需要分别核验。
- [Bilibili 资料](https://www.bilibili.com/opus/575170578495173482)：返回验证码页，未读取内容；[知乎资料](https://zhuanlan.zhihu.com/p/512303214)返回 403，未读取内容，不绕过。
- [Feeder](https://feeder.co/) 可提供第三方发现及历史更新线索，部分页面明确标注自动生成 feed；其报告不能替代当前环境直接解析 XML、版权确认及内容审计。

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
| BBC World | 英 | https://feeds.bbci.co.uk/news/world/rss.xml | 用户附件及第三方目录；工具识别 XML，未解析 | 综合源在实测有效后建议保留，不提高优先级；其他 BBC 独立分支建议移除 |

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

## BBC 配置策略：待确认，尚未实施

保留唯一的 BBC World 综合 RSS，前提是能实际读取并解析、条目正常更新且条款适用于用途；不为 BBC 设加分或配额。BBC Africa 等其他 BBC 栏目建议从实际运行配置中删除或合并到 World，单改公开模板无法生效。当前 `CONFIG_JSON` 未读取、未修改：生产配置是否包含 BBC Africa、其他 BBC 栏目或相同 URL 的变体均未知，不能声称已清理。BBC World 同一 URL 在第三方聚合转载的单篇新闻不是额外添加的 BBC RSS 分支；按通常去重和来源归属处理，不恢复旧的 BBC 全站屏蔽方案。

用户审核并批准后，由有权限的用户自行调整 Secret，或另行授权具体修改方式；先提供可审阅的清单和脱敏对照，不读取、打印或提交 Secret。离线验证配置方式覆盖 BBC World、BBC Africa、URL 变体、旧配置兼容、无优先级/配额、正式 EPUB、Shadow、`test_mode` 和去重隔离。删除非 BBC 来源或阻止第三方报道中提到 BBC 不在此方案中。

## 拟议来源分级与后续验证

- **已独立验证当前 XML 有效且更新：无。** 本次工具不支持解析 XML，不将 MIME 识别当作通过。
- **待优先验证**：Le Monde Une 与 International；AP Flipboard Top Stories；Guardian World；France 24 一种综合语言；RFI Afrique；NPR World；Al Jazeera；DW；Middle East Eye；African Arguments；Reporterre 的网络可达性。
- **需版权/AI 使用与归档范围明确后再评估**：Washington Post World、AP/Flipboard、Le Monde 及其他提供者；专业 AFP、新华社供稿必须核准授权与价格。
- **暂缓或建议放弃**：BBC Africa 及其他 BBC 专业分支（BBC World 经验证后保留）；AFP 机构活动 feed；旧 AP 源；无证据持续更新的旧 Xinhua RSS；新闻以外的博客、随笔及仅含音视频的源。当前公开模板中的 Hacker News、阮一峰日志、少数派等是示例而非生产清单，不可据此声称它们正在运行。

推荐的第二阶段工作顺序：先确认允许以何种方式获得脱敏运行数据并核对主要源权利；使用经授权、正常可访问的网络完成 7–14 天 feed 数据和源内/源间重复对照；提交具体保留/移除清单及回放结果；用户批准后才按确认的方式调整生产 BBC 分支配置，并视回放结论实施候选时间排序，并对正式 EPUB、RSS 去重、WebDAV、Memory 及 200 cap 做回归测试。任何受限或失败的源只记录状态，不尝试绕过访问控制。

**预期与风险：** 仅保留 BBC World 可能减少 BBC 地区报道，但无需阻止该综合源；按来源时间排序可能提高新鲜度但不能保证事件重要性；精简分支可能减少重复和某媒体的轮询权重，但也可能丢失重要独家报道。若源实际不可访问、条款不适用于自动存档或 AI、摘要稀少、feed 内含视频或发表时间不可靠，实际收益可能低于预期。
