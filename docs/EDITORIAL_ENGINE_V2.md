# Editorial Engine V2：离线评测与设计（Draft）

> 后续采集更新：默认关闭的真实采集和加密存储见 [Benchmark 采集说明](EDITORIAL_BENCHMARK_CAPTURE.md)。该方案增加 `private` 密文输入、来源权限和 B0/B1 实际覆盖；以下第一阶段协议的“没有生产采集接口”描述仅适用于原始 PR #11。未启用前仍不会采集。

本阶段交付**隔离评测基础设施**，不是新的生产选稿器。正式 RSS、EPUB、投递、AI、去重及 Memory 没有接入。合成数据的对照只验证计算和数据契约，不能证明编辑质量改善。当前没有经授权的固定真实评测集或实际 B0/B1 逐篇运行结果。

## 冻结基线和可重现性

以 main commit `e6d33ebaee53dbe7bdeffdb83bc6943961d4cc08` 的代码及各次实验实际配置快照为基线：

- **B0 传统出版**：`src/main.py::_fetch_source_task` 根据历史去重、来源限制选择候选，经抓取、内容处理和有效性判断，最后提交出版；具体媒体、配置、时间和源限制均影响结果。传统流程没有事件聚类输出，不能把出版文章视为其聚类结果。质量拒绝、配额未尝试、访问失败分别记录。
- **B1 Topic-first Shadow**：`CandidateCollector` 在抓取质量验证之前观察 RSS 候选，按来源名称轮询、来源内哈希排序；`src.ai.editor.run` 在 cap 内 Grouping → 多文章组 Audit → Selection，失败时安全降级，不影响 B0。候选池可能含未验证内容，也可能与 B0 文章集合不同。版本、模型、提示词、预算、Memory 状态以及输入顺序都须固定并记录；不得公开 Memory 内容或将其发送至未授权目标。

仓库代码不锁定动态模型服务行为。B0/B1 必须以隔离、授权环境中**记录的实际结果**作为快照回放，真实基线缺项报告不可比较，不能借本工具虚构基线或调用现有生产入口。B1 后续需对已授权输入采集完整、经校验的聚类及选稿结果；服务非确定性须重复试验并保留各次结果。仓库测试的 `B0_fixture` / `B1_fixture` 都是合成伪结果，并非真实测量。B0 没有聚类时相应指标为 `null`。

## 数据契约 v1 与安全边界

`scripts/editorial_v2/replay.py` 校验 JSON：`schema_version=1`、`data_class`（`synthetic` / `approved_public`）、`candidates`、`gold`、`recorded_results`。每个候选必须包含唯一 `candidate_id`、`source`、`language`、`published_at`（带时区 ISO 8601 或 null）、`title`、`excerpt`、布尔 `excerpt_permitted`、`media_type`、字符串数组 `media_evidence`；可选 `url` 必须具有处理授权。未获摘要处理许可时 `excerpt` 必须是 null；字段白名单禁止附带正文、私人状态、质量结果或隐藏标签。URL 不应公开，合成捕获器不采集 URL。正文质量属于各算法运行后的 `recorded_results[*].quality`，不能作为选择前候选输入。

质量枚举为 `unknown`（状态不可知）、`unattempted`（明确未尝试）、`quota_limited`（因配额未尝试）、`failed`（已尝试失败）、`rejected`、`summary`、`rss_content`、`full_text`。媒体类型 `text` / `media` / `mixed` / `unknown` 与判定依据分开；带媒体附件不是纯媒体的证据。缺发表时间用 null；不得以抓取时间替代。当前适配器不把这些状态推断成新状态。

`gold` 是 candidate ID → `{event_id, important}` 的人工标注映射，可部分缺失；同事件的重要性必须一致。真实评测需要独立标注手册、证据、标注人一致性与争议仲裁。`recorded_results` 包含 `status`（ok / partial / failed）、`observed_ids`、有序 `selected`、恰好划分已观察候选的 `clusters` 或 null、各算法自己的 `quality`，以及可为 null 的 `elapsed_seconds`、`cost`、`input_tokens`、`output_tokens`。`ok` 须覆盖所有候选；cap 或提前停止用 `partial`，未观察候选不能伪装为单例；失败不能声称已有选稿或聚类。聚类指标仅在已观察且有标签的候选上计算，报告观察数量；重要事件覆盖以全部已标注事件为分母，不能据此把部分聚类描述为全量成功。时间和成本是实验提供的测量值，不由回放伪造；模型单价、币种和时间测量口径需另附实验元数据。未标注候选在覆盖、重复、聚类中不会被当作负例；分母及标注数必须随指标查看。

**安全限制**：工具只接受显式声明的合成或有权公开处理数据；声明本身不是许可验证。生产/私人数据不得作为输入写入公开日志、Artifact 或 Git，亦不得将其伪标记为公开。CLI 只输出聚合指标及固定错误，不输出候选文本；小样本聚合仍可能被反向识别，只可对审查通过的数据公开。工具无联网、配置、凭据或生产导入路径；不提供生产快照采集。使用获得授权的数据、元数据脱敏和存储隔离须由用户另行审批。无权向 DeepSeek 等第三方传送私有来源、阅读记录或未授权摘要。

## 离线回放

```sh
python -m scripts.editorial_v2.replay tests/fixtures/editorial_v2/synthetic_snapshot.json --ordering id
python -m scripts.editorial_v2.replay tests/fixtures/editorial_v2/synthetic_snapshot.json --ordering newest
```

三种顺序：原始、ID、发表时间降序（无日期最后，ID 稳定平局）；这些仅是离线排序实验，不更改生产顺序。CLI 对现有记录结果按选择读取，顺序变化不会重跑记录的历史算法，不能因此声称顺序稳定。Python `replay(snapshot, engines, ordering)` 允许接入独立离线算法回调：每次给回调深拷贝的候选，不给 gold，再对其输出验证；调用者负责算法隔离和不得外发。固定输入、算法、结果及计量值时指标确定；无法把模型耗时或在线模型响应保证确定。该接口是开发实验的可扩展边界，不是生产代码钩子。

## 指标定义

- 重要事件覆盖：入选且有 gold 的独立重要事件 / 已标注重要事件；所有重复报道共享事件标签。
- 聚类 pairwise 精确率/召回率/F1：仅比较均有 gold 标签且实际被该算法观察的候选对；输出 TP、FP、FN、分子分母，F1=2TP/(2TP+FP+FN)，真实正例存在而未预测正例时 F1=0；分母为零时为 `null`。
- 事件集中度（兼容字段 `duplicate_rate`）：入选且有 gold 标签的候选数减其中独立事件数，再除以有标签入选数；它不等于无价值重复。`incremental_labels[后文章ID][先文章ID]` 单独给出有方向的 `no_new_information`、`substantive_update`、`new_information` 或 `unknown`。仅当每个同事件先行比较都有标注，才计算无增量重复数 / 总入选数；标签缺失返回 null，跨顺序不能复用反向标签。
- 重要单例遗漏：候选集中仅一条有标签候选的已标注重要事件中未被选中的比例。它受未标注数据影响，不能证明全球单例。
- 内容质量：展示选中各状态数量、已确认全文数和全部入选数；只在质量状态完整时计算 full_text/rss_content 比例。存在未知、未尝试或配额未处理时比例为 null，摘要不当作失败或已验证全文。重要性与质量分开。
- 时间、token 和成本：仅采用记录值；未知为 null；人工报告计价、currency、版本和异常。

## 实验协议与停止条件

1. 先固定候选、许可、收集窗口、标注指南和盲评；版本化输入与标注，在授权私有环境对快照作完整性摘要（不要公开可反推私有内容的哈希）。按事件而非文章拆分开发/保留测试集，控制时间泄漏；验证中英法、来源与现实事件多样性，不机械设选稿配额。
2. 比较 B0、B1、未来 V2；相同候选、容量、合法可用信息、质量验证和 token/时间上限。B0 仅观察到出版子集、B1 可能 cap 或失败时必须展示共同可观测子集及缺失，不能用不完整的标签装作全覆盖。B1 真实调用必须单独授权；本 PR 不调用。
3. 消融关系识别、媒体筛选、质量预算、排序和边际选择；逐项固定其他组件，人工核验误合并/拆分；检查多种输入顺序、重复测试和近似置信区间。模型使用的预估与实际 token、失败、成本与时长一并记录；比较不得只依文章数或报道次数。
4. 缺合法数据、标签不足、许可不明、数据泄露、预算超限、配对失败、误合并重大事件或主要指标退化时停止并调查，不进入生产。正式阈值、评委和样本量由用户在持有固定数据后预先确认；不能根据结果事后改标准。所有费用/API、生产变更另行批准。

## B2 确定性离线基线

`python -m scripts.editorial_v2.replay tests/fixtures/editorial_v2/synthetic_snapshot.json --b2` 实际运行本地 B2，不请求 API。允许的 URL 仅去除明确列出的跟踪参数并保留其他查询参数原始编码及顺序；无 URL 时不能做 URL 同稿判断。其余合并要求同语言、可靠发布时间相距不超过 36 小时、至少两个共同专名及标题三字符 n-gram Jaccard ≥ 0.72；完全链接规则防止弱桥接。无效、无日期或超出截点 5 分钟的未来日期不参与时间关联并排在有日期后。按时间和 ID 排序，每组只选首篇至容量；这是简单冗余控制，不是国际重要性估计、真实语义证明或跨语言模型。标题格式、拼写和单例选择均有误差，必须通过固定集盲评，不能据合成示例断言质量。

## 隔离采集及存储设计（尚未启用）

`capture.capture_synthetic` 只接收调用者给定的合成 `CandidateCollector.snapshot()` 形状数据和固定截点，以及分别提供的 B0/B1 状态、观察 ID、选择、聚类和质量映射；它不读取生产配置、质量文件、存储或网络。`media_hint` 仅记录证据并保持媒体类型 unknown，解析不到的发表时间保留 null。实际接口设计可在隔离进程接收现有 Collector 的截点拷贝，使用质量报告和 Shadow 报告按 ID 对齐 B0、B1 覆盖及质量；如果关联不可靠、报告缺失或未尝试，则保留 unknown，不能把不存在的覆盖推定为零或补成单例。Collector 在质量验证前创建候选，不能将后续 B0 内容抓取结果喂给其他算法；URL 另需使用权限，Collector 哈希不能跨源证明同稿。

未来需要用户先授权候选数据和来源条款、选定明确的截点与私有保留期限、准备独立的 32 字节密钥与隔离 WebDAV 目录，再单独审核实现并运行一次合成端到端测试。建议参考 `src/ai/memory/crypto.py` 的 HKDF + AES-GCM 与 `storage.py` 的已验证写入/旧版恢复设计，但现有 `encrypt`/`decrypt` 强制校验 Editorial Memory，不能直接序列化候选；应使用独立版本头、独立 HKDF context 和独立密钥，不修改 Editorial Memory 格式、目录、密钥或出版目录。仅密文上传，敏感标题/URL/摘要和可反推的固定哈希均不得写公开日志或 Artifact。许可不足、缺密钥、目录重叠、加密/上传/校验失败时不记录明文、不开启回退到公开存储、不影响日报；读取损坏时保留原文件并停止该次实验。保留期限由用户配置并验证密文删除，历史备份及服务端副本仍需单独管理。当前不创建密钥、写状态、连接 WebDAV 或启用真实采集。

## 后续架构接口，不实施

关系集合包括 `same_item`、`same_event`、`substantive_update`、`related`、`different`、`unknown`。保守 URL 归一化保留语义查询参数；弱关系不得经传递闭包无限并组。高置信同稿可去重，其他不确定项保留；审查 Africanews 附件/正文分类及多语评测，既测误合并也测误拆分，不因来源单一、缺日期或质量未知直接淘汰。轻量文本方法、现有 LLM 及可选本地多语挑战方案需独立实验并报告许可、体积、CPU 时间与效果，不引入常驻服务、向量数据库或新付费服务。

后续按事件证据定义 A/B/C/D 重要性和不确定性，独立评估文章质量，以合法 ID 引用输入证据；在固定候选、验证和日报容量预算下，按边际信息增量做确定性贪心选择及有限替换，不用报道数量作硬阈值或设置国家/媒体/语言配额。具体阈值与算法另行评审，不在本阶段实现。

Event Memory 设计为独立的版本化状态接口：`lookup(event_key, as_of)` → 未知/既有主线及可公开证据引用；`propose(event, evidence, observed_at)` → 不可变变更建议；`apply(patch, expected_revision)` → 乐观并发控制结果。状态区分首次发现、同事实重复、实质更新、未知和过期；时间及证据来源必须可追溯。测试接口要求并发冲突、乱序/过期、未知、跨日更新、损坏只读回退及禁止把未知当重复。它与 Editorial Memory 的偏好配置、URL Dedup 的出版历史分别存储，当前不读取或修改任何一种生产记忆。

## 研究参考（非实现或效果背书）

Google News “How News Works”；[Miranda et al., 2018](https://aclanthology.org/D18-1323/) 多语流式新闻聚类；[SemEval-2022 Task 8](https://aclanthology.org/2022.semeval-1.131/)；Hanley & Durumeric (2025) 层级新闻聚类；[Carbonell & Goldstein (1998)](https://doi.org/10.1145/290941.291025) MMR；Lin & Bilmes (2011) 子模摘要；NIST TREC Temporal Summarization；2019 recency-relevance-diversity 新闻推荐研究。论文方法的许可、适用性和数据权限须在实际实现前核验。
