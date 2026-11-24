# PR #8：来源事件增量与运行 #31 候选审计

本报告只记录审计、离线验证和待审批设计，不修改生产 RSS、EPUB、去重、AI 编辑器、WebDAV、Secrets 或 Variables。公开 RSS 来源仅用于审计，不代表生产配置或实际推荐。

## 数据与可复核性

- 公开 RSS 快照 A：[run 45244541615](https://github.com/MVVVVK/OughtGather/actions/runs/45244541615)，北京时间 2026-11-24 03:01；B：[run 45301867050](https://github.com/MVVVVK/OughtGather/actions/runs/45301867050)，同日 09:02。每次 49 个公开 feed 中 41 个成功解析 RSS/Atom；A 和 B 间隔约 6 小时，**不是多天趋势，也不证明持续更新或正文可用性**。两次的脱敏汇总分别是 [A](data/rss_probe_2026-11-24.json)、[B](data/rss_snapshot_2026-11-24_0902.json)；[phase3 离线 JSON](data/rss_phase3_redacted_2026-11-24.json) 的 `public_comparisons` 只来自快照 A；它不是双快照数据，也不包含 B 的人工样本。双快照表是分别取 A、B 两份结果后人工编排，其 B 的 BBC 5/20 是单独的前 20 条样本，不能从单快照 JSON 重算。原始公开 RSS 的正文和摘要未写入报告；两次仅对 robots 允许且直接可访问的页面采集，未绕过访问限制。
- B 的人工样本来自 [run 45301867050](https://github.com/MVVVVK/OughtGather/actions/runs/45301867050) 中 `rss-public-diagnostics` → `Inspect bounded public RSS titles` 步骤（约北京时间 09:02:25–09:02:28）；`public_samples.py` 从 18 个白名单公开 RSS 按 feed 返回顺序各取前 20 条，标题最多 180 字符，文章链接去除查询参数和片段，未存摘要或正文。这些是**公开来源样本**，与生产订阅和阅读记录无关；日志可访问性及保留期由 GitHub 控制。[样本证据清单](data/rss_public_event_samples_2026-11-24.json)逐项记录公开 feed URL、样本序号、去参数后的文章 URL、观察时刻、比较类别及不确定性，不含正文。序号是 feed 前 20 条中通过标题/链接过滤后的返回样本顺序（从 1 起），不保证等于原始 RSS 行号。表中“相同 URL”指采样器去参数后的 URL 相同，不一定证明原始 URL 字符串逐字一致；公开文章链接可复查页面，但页面可能更新、受地区或订阅限制，正文差异仍需在合法可访问条件下人工核验。
- [运行 #31](https://github.com/MVVVVK/OughtGather/actions/runs/45298550652) 的来源质量及 AI 记录汇总为[运行级合计](data/rss_phase3_redacted_2026-11-24.json)。本分支当前版本不发布逐源计数，但旧版本及运行日志仍可能允许反向识别，见隐私风险一节。生产 `CONFIG_JSON` 未读取或公开；候选总数相同不等于已逐条确认来源归属。

## 同窗口比较：精确重合是下限

每个快照只比较 feed 当时返回且发表在过去七天内的有限条目，不是完整七天出版历史。下表是**相同 URL 或规范化标题完全一致**的条数；分母是右侧栏目样本数。同一事件可能有不同 URL 或标题，不匹配不代表独立；标题相同也不自动证明事件相同。括号标出 B 的栏目分母。

| 综合源 → 专业源 | 快照 A 精确重合 / 栏目条数 | 快照 B 精确重合 / 栏目条数 | 解读 |
|---|---:|---:|---|
| Le Monde Une → International | 6 / 20 | 4 / 20 | 确认有同稿跨栏目；其余 `unverified` |
| Une → Justice | 1 / 20 | 1 / 20 | 其余是否具独立国际价值 `unverified` |
| Une → Économie | 5 / 20 | 4 / 20 | 同稿交叉，报道角度需人工比较 |
| Une → Les Décodeurs | 0 / 18 | 0 / 18 | 零精确命中不是零事件重合 |
| Guardian World → Business | 2 / 40 | 3 / 40 | 跨类别有同稿，未匹配不等于新事件 |
| World → Technology | 2 / 24 | 2 / 24 | Business/Technology 间也发现同稿 |
| World → Science | 0 / 22 | 0 / 22 | 播客/视频不能当成文字增量 |
| World → International Development | 3 / 12 | 1 / 12 | 可能补充发展议题，仍需事件核验 |
| France 24 English → Africa EN | 1 / 14 | 2 / 14 | 除同稿外还有同事件不同形式 |
| France 24 Français → Moyen-Orient | 1 / 15 | 1 / 15 | 无文字正文的节目不能据栏目保留 |
| RFI Français → Afrique | 5 / 24 | 10 / 24 | 同稿重合随快照变化；单次无法判定全部增量 |
| BBC World → Africa | 7 / 17 | **5 / 20** | A 为完整 feed 窗口；B 只人工比较两源各前 20 个标题，分母和方法不同 |

France 24 英/法综合源 A 的精确命中为 0，但两语言同事件样本见下表；不能因此宣称无重合。来源完整性、单源截断和不同语言都限制推论。

### 两份数据的证据边界

`phase3_offline.py` 的 `--public` 一次接收一个 `rss-diagnostic.json`，`audit()` 报告 `public_snapshot_count=1`；
本分支的 `rss_phase3_redacted_2026-11-24.json` 中 `public_comparisons` 也是 A 单快照的 15 对精确重合，
并未输入 B，也不包含标题相似、不同标题同事件、独立信息增量或人工判断。B 单独的 `rss_snapshot_2026-11-24_0902.json`
是 11 对精确比较；B 的 BBC World/Africa 5/20 来自上文所述标题采样日志，而非该 JSON 的自动比较。
仓库内 A、B 汇总不保存逐条 URL/标题指纹；单快照离线比较需要当时 Artifact 中包含 `items` 的诊断数据。
仅用仓库内汇总不能重新计算这 15 对结果；缺少 `items` 时工具返回 `unverified`，不会伪造零重合。
已提交的 phase3 JSON 是当时的脱敏审计汇总，而非当前工具输出格式的逐字重现；原始 Artifact 可用性和保留期限需单独核实。
“双快照表”是各自结果的并列展示，不是该离线程序生成的跨快照去重、语义匹配或增长率。
两次 `within_7_days` 分别相对各自抓取时间计算，且每次 feed 本身有限条目；这不构成持续 7 天或多日面板。

## 有限事件样本：已观察与未验证

以下表内样本均为上文 B 的前 20 条公开 feed 样本；同 URL 样本通过去查询/片段后的链接比对，不同标题/语言案例依据标题中的主体、行为、地点或金额进行人工判断。Guardian 的新闻/评论类型通过其公开网页页面标识确认；其余事件关系仅是标题级判断，不能证明原文事实或全文质量。正文层面的独立信息增量一律 `unverified`。France 24 和 RFI 受检索工具访问限制时只使用已公开 RSS 标题，不绕过限制。

| 类别 | 可复核样本 | 观察和边界 |
|---|---|---|
| 相同 URL | [BBC 对利比亚舰船袭击的报道](https://www.bbc.co.uk/news/articles/cr894gjz0j0yo) 同时出现在 World 和 Africa 前 20 个标题中 | 同一稿件，不是两个独立事件；BBC Africa 的移除建议来自用户偏好，不是说其内容质量差。 |
| 相同 URL | [Le Monde 农药学校缓冲区报道](https://www.lemonde.fr/les-decodeurs/article/2026/11/20/pesticides-des-parents-d-eleves-somment-l-etat-d-elargir-le-perimetre-de-non-traitement-autour-des-ecoles_6813595_4355770.html) 同时出现在 Justice 和 Les Décodeurs | 同一篇文章被两个专业分类收录；重复 URL 只证明这篇，不能推断两个栏目整体相同。 |
| 相同 URL | [Guardian Nvidia 新闻报道](https://www.theguardian.com/technology/2026/nov/23/jensen-huang-reinstated-as-nvidia-chief-executive) 同时出现在 Business 与 Technology | 栏目数增加不等于新闻主线增量。 |
| 相同 URL | [RFI 的利比亚方面回应](https://www.rfi.fr/fr/europe/20261123-direct-l-ukraine-dit-avoir-attaqu%C3%A9-deux-bateaux-russes-pr%C3%A8s-des-c%C3%B4tes-de-la-libye) 同时出现在 Français 与 Afrique | 同稿重复可确认；源内其它未匹配项目仍需审阅。 |
| 不同 URL、标题高度相似 | France 24 Africa 的[塞内加尔总统被移出执政党报道一](https://www.france24.com/en/senegal-pastef-removes-president-faye-from-its-ranks)和[报道二](https://www.france24.com/en/bassirou-diomaye-faye-excluded-from-senegal-s-ruling-party) | 标题的人物、政党和动作相同，可能为同一事件；正文是否为重复或各有新增事实 `unverified`。 |
| 同事件、不同标题/语言 | France 24 [英文报道](https://www.france24.com/en/iran-s-courts-order-us-to-pay-290-billion-in-damages) 与 [法文中东栏目报道](https://www.france24.com/fr/moyen-orient/20261123-t%C3%A9h%C3%A9ran-r%C3%A9clame-290-milliards-dollars-d%C3%A9dommagements-washington-guerre) 均涉及伊朗方面向美国索赔 2,900 亿美元 | 标题中的主体、对象和金额吻合，同一主线的高置信标题级判断；具体正文差异 `unverified`。 |
| 同事件、不同标题/形式 | France 24 Africa 中 [有关袭击舰船的报道](https://www.france24.com/en/africa/20261122-russian-warship-hit-by-suspected-ukrainian-drone-at-libya-s-benghazi-naval-base)，与综合源中的 [有关同次袭击的讨论节目](https://www.france24.com/en/tv-shows/the-debate/20261123-how-far-will-ukraine-war-spread-kyiv-targets-russian-vessels-in-libya) | 同一利比亚舰船袭击主线，不同标题和节目形式；节目是否有独立可读文字 `unverified`。 |
| 同事件、有不同视角 | Guardian 的 [Nvidia 复职新闻](https://www.theguardian.com/technology/2026/nov/23/jensen-huang-reinstated-as-nvidia-chief-executive) 与 [董事会评论](https://www.theguardian.com/technology/nils-pratley-on-finance/2026/nov/23/nvidia-board-jensen-huang-return) | 公开页面明确一篇是新闻、一篇是评论，提供不同编辑视角；不能机械说内容完全重复，但同一事件不应占两个主题名额。新增独立事实的价值 `unverified`。 |
| 可能增量 | RFI 的 [利比亚方面要求解释](https://www.rfi.fr/fr/europe/20261123-direct-l-ukraine-dit-avoir-attaqu%C3%A9-deux-bateaux-russes-pr%C3%A8s-des-c%C3%B4tes-de-la-libye) 与 [利比亚内部各方紧张局势](https://www.rfi.fr/fr/afrique/20261123-l-attaque-ukrainienne-en-large-de-benghazi-ravive-la-tension-entre-les-bellig%C3%A9rants-libyens) | 标题显示同次袭击的不同后续角度；未经全文核实，独立信息增量 `unverified`，不可据此保证一个栏目值得永久保留。 |
| 独立主线 | Guardian [利比亚舰船袭击](https://www.theguardian.com/world/2026/nov/23/ukraine-naval-drones-strike-russian-warship-libya) 与 [Next 网络攻击](https://www.theguardian.com/business/2026/nov/23/next-issues-cyber-attack-warning-after-benign-data-accessed-by-hackers) | 标题/公开网页所涉对象和事件明显不同；不能因同日或同属 World/Business 而合并。 |

这些是选择性样本，不能估计整体语义重复率或保证分支信息增量；法国司法不同案件、不同疾病/攻击等仍需逐对判断。跨语种相似、未抽中的分支及限制页面都标记 `unverified`，不将全文不可访问当作相同或不同事件的证据。

## Daily Gather #31：运行级统计与可反向识别风险

本次在有仓库访问权限的连接器中检查了运行 #31 的 job log：8 条来源抓取统计行中均含来源 URL 线索，
其中 `RSS 返回数 + 历史去重数` 与此前发布的 F01–F08 八组记录**各有一个唯一匹配**。因此编号与逐源计数并不匿名，
可以从可访问日志反向关联来源；这里不展示映射。此检查证实连接器可读日志，**没有测试未登录用户是否能读取**。
运行 Artifact 元数据表明 `quality-report`、`ai-editor-report`、`logs`、`dedup-records` 当时存在且未过期；
运行工作流配置显示 `quality-report` 上传 `quality-sources.json` 与 `quality-report.json`，代码会写来源 URL 与文章级 URL。
本次未独立解压该 ZIP、未逐个验证其内容或匿名访问权限，因此这些是工作流/代码和元数据支持的风险，不冒称已验证 ZIP 内容。
有权访问原始 Artifact、日志或旧 PR 提交的人可能交叉关联来源与阅读活动；公开仓库不应默认 Artifact/日志不可访问。

为降低新版本暴露，本报告及当前 [JSON](data/rss_phase3_redacted_2026-11-24.json) 移除 F01–F08 的逐源行，
离线生成器默认只输出运行级合计及来源数；这**不是完全匿名化**。旧 Git 提交、旧 CI 日志、已下载副本及现有 Artifact 不会因新提交自动消失。
本任务没有删除、重写或修改任何历史记录、Artifact、Secrets 或生产配置。仓库所有者可在审阅其实际权限和保留要求后决定是否限制访问或清理；
强制改写历史或删除记录必须单独评估副作用。若未来确需逐源诊断，建议仅在用户控制的私有环境内做内存关联，
对外发布最小必要的聚合；随机编号、去 URL、哈希或单独小计均不能消除可关联性。
离线工具对少于三个来源的总量返回 `null`，但三个及以上来源的汇总也可能与其他数据关联，并不能保证匿名。
GitHub 上当前 PR 的旧提交已公开，普通 merge 还会把它们纳入 main 的祖先；squash merge 只避免将这些提交作为 main 的直接历史，
不会自动删除原 PR 分支、提交、日志、Artifact、缓存或已有副本。若这些历史关联风险不能接受，应以最新 main 为基线另建只含已审查当前文件的干净 PR，
但该措施也不会自动清除原 PR 历史；是否限制访问、联系 GitHub 或清理记录由仓库所有者单独评估，不能保证收回已有副本。

来源汇总为 RSS 条目 **247**、历史去重 **143**、候选 **104**、媒体提示 **22**、尝试 **14**、配额未尝试 **90**、
质量通过 **14**、正式入选 **14**。媒体提示是元数据而非确认视频；配额未尝试不是质量失败。
只保留运行级数据也可能在极小群体或与其他公开数据组合时泄露信息，不能保证不可重识别。

14 次尝试的来源汇总为 10 篇网页全文、4 篇摘要、0 篇 RSS 全文、0 次 `failed`；这是来源报告计数，不证明 90 篇未尝试文章的质量。AI 报告为 104 个候选、73 个复核后主题、16 个建议、14 个传统入选，cap 200、超限跳过 0。故**此运行没有因 200 上限挤掉任何候选**；不能据此断言媒体候选是否进入具体推荐，也不能量化其对推荐质量的影响。22 个媒体提示是来源级统计，不是人工确认的 22 个纯视频/音频/直播。

来源计数总和与 AI 总数都是 104，但源级计数与 AI 逐篇对应关系、未入 AI 的具体来源、因正文拒绝而入 AI 的篇数仍为 `unverified`。对失效来源、缺字段或单阶段回退，诊断工具保留 `null` 作为未知，不把未知归零。

## 代码结论与候选池设计（待审批）

以下代码与运行 #31 对应 commit `760b1c03` 的 `src/main.py`、`src/ai/candidates.py`、`src/ai/editor.py`、`src/fetchers/rss_fetcher.py` 的 blob 一致：`collector.collect(new_candidates)` 在 `fetch_items()` 质量审核之前；每源按候选哈希排序后轮询来源；AI `run()` 再截断前 200 并且只提供标题/来源/语言/发表时间/摘要。媒体提示和正文结果不会自动成为模型输入。离线测试证明媒体提示候选**可能**占据候选前 200；生产规模、丢失多少高质量条目尚未量化。

候选设计只供评审：

1. 在不影响正式出版的独立 Shadow 准备阶段，复用本轮已产生的明确质量状态；标记已知纯媒体且无独立文字、验证码/空提取等明确拒绝。保留 RSS 合法全文与摘要。`quota_limit`、未尝试和来源失败保持 `unknown`，不得当作失败或永久去重。
2. 对 unknown 候选可先以 RSS 文本和元数据做保守资格检查；如额外获取正文，须另行设计小规模、有次数/时间上限、遵守 robots/权限、可禁用且绝不修改正式去重的验证，不阻塞日报。网络失败与暂时性访问限制遵循现有失败重试/冷却语义，不能永久丢弃。
3. 同源队列内对可可靠解析的 **published** 时间排序，稳定 ID 作相同时间 tie-break；缺失、非法、显著未来日期归为未知并保留可回放机会。不要把 RSS `updated` 或抓取时间冒充发表时间；本次 DW 有 139 条缺少可解析发表时间，简单一律排到队尾可能伤害该源。未来容忍窗口和未知与已知的混排须通过模拟和历史数据回放后定，不设置地区或媒体硬配额。
4. 继续来源轮询与稳定顺序，在 cap 前保守处理相同规范化链接和确证同稿，不以相似词、不同语言或同类别武断合并事件；保留同事件不同报道视角供最终唯一代表选择。处理同名 source、不同 feed 与跨源转载时先定义出处与统计，不以 hash 排名当新鲜度。
5. 离线测试覆盖有效/媒体/验证码/摘要/未尝试、失败重试、缺失/未来日期、同源同时间确定性、不同来源轮询、同链接和不同链接同事件、200 上限边界，以及对正式 EPUB、去重、WebDAV、AI 编辑器的字节级/状态隔离。当前没有实施该候选算法。

为量化生产影响，未来仅在**用户批准后**于用户控制的私有环境对质量与 AI 数据进行内存关联，区分未尝试与失败、缺失字段为 null；不对外输出逐源小计、可关联文章 ID 或映射。对外只发布经过风险复核的最小运行级总量；即使随机 source ID、汇总分桶或少量源分组也可能与现有日志关联，不能承诺绝对匿名。此提案不更改正式运行或收集新数据。

## 拟议来源决策与实施顺序

| 分类 | 建议（全部待用户审批及生产清单核对） | 证据 / 未决 |
|---|---|---|
| **A 拟保留** | BBC World；Le Monde Une；Guardian World；France 24 与 RFI 的综合源候选 | 两次快照有近期条目，BBC World 符合用户偏好；这不证明长期稳定、全文或授权。英法版各自信息增量待人工核对，不设语言/媒体配额。 |
| **A 条件保留** | Le Monde International、RFI Afrique、Guardian Development/Technology/Science/Business、France 24 地区栏目 | 已见一些同稿、同事件及未匹配标题；独立信息增量与长期采样仍 `unverified`，不得凭 URL 差异或宏观话题作结论。 |
| **B 建议移除或合并** | BBC Africa 与其他 BBC 专业 RSS；AFP 机构公告作为世界新闻的用途；新华旧英文 2018 年静止 feed | BBC 是用户选择而非质量判断；AFP 被证实为机构活动，旧新华流停止更新的长期状态应复测。**不假设它们存在于生产**。 |
| **B 暂缓决定** | 其他 Guardian/Le Monde 专业栏目、France 24 双语或地区支流 | 没有足够事件级证据，不以每家一个源为机械上限；不删除现有生产配置。 |
| **C 待补充验证** | AP 官方账号经 Flipboard 的 Top Stories、新华社合法公开订阅或明确标注的第三方线索、AFP 经授权新闻内容 | 不是已批准正式源；需验证长期更新、原始报道归属、条款、转发/外部 AI 权限与正文可读性。AFP 机构公告不是国际通讯稿。 |
| **C 授权后评估** | Washington Post World | 虽有可解析 RSS，官方 RSS 和网站条款对存档、自动采集及 AI 使用有要求，先确认使用范围；不得绕过付费墙。 |
| **个人阅读** | The Conversation、Project Syndicate、The Atlantic、个人博客等 | 可在独立个人阅读库保留；是否为当天有独立信息价值的新闻，需逐项判定，不自动进入日报主源。 |

英语组合可优先审阅 BBC World、Guardian World、合规可用的 AP、Al Jazeera、NPR、DW 以及中东/非洲独立报道；法语可审阅 Le Monde Une、France 24、RFI，并以证据决定 International/Afrique 等少量增量。它们是候选组合而非立即修改；按全球新闻价值与实际可读性选择，不设来源、国家或语言配额，也不因偏好自动加分。

实施顺序：①用户审阅本报告及授权边界；②用户控制或批准的匿名来源统计与多日事件级抽样；③离线回放时间排序、媒体质量和 200 cap 变化并评估回归；④用户再次批准具体生产清单与实现；⑤再做隔离变更、完整测试及手动验证。当前只提交审计和离线工具，Draft PR 不合并。

## 测试记录校正

[CI #44（run 45302916470）](https://github.com/MVVVVK/OughtGather/actions/runs/45302916470) 的 GitHub Runner 主测试实际为 **739 passed、4 skipped**（14.25 秒），另有审计专项 **15 passed**；先前报告的 **741 passed、2 skipped** 是本地环境结果，不能标作该 CI 的统计。不同依赖或运行环境造成的跳过项目差异应分别报告；不据此推断跳过原因。
