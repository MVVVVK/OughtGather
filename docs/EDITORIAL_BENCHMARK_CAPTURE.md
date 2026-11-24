# 私有 Editorial Benchmark 采集（Draft）

默认关闭。该功能只在用户明确授权及合并部署后运行；本 PR 不启用配置、不访问生产 Secrets、不执行真实出版或真实 WebDAV 写入。本功能基于已合并的 PR #11 评测基础设施。

## 授权表

| 字段 / 用途 | 来源 | 本实现的收集规则 | 用户尚需确认 |
| --- | --- | --- | --- |
| 来源标签、候选 ID、标题、发布时间、采集时间、媒体提示 | 现有 RSS CandidateCollector；候选为历史过滤之后、B0 正文质量检查之前 | 每源 `title: true` 才收集，未配置默认整源排除；不保存 URL 或正文；媒体提示只记录启发式证据，不判定纯视频 | 来源使用条款、私有保存和标注是否获准；可否收集标题及来源 |
| RSS 摘要 | 同上 | 同时需该源 `excerpt: true`；否则不复制、不存储，Shadow 输入也清空 | 摘要的私下存储、处理和向模型发送权限 |
| B0 选稿及质量状态 | 同次质量报告，通过既有 source-key + URL 计算的候选 ID 在内存中对齐 | 仅匹配获准候选；抓取后结果单独存放，不进入算法输入；未知、未尝试、配额限制与失败分开 | 质量报告中来源与 URL 的授权；对齐失败时不推断为未选 |
| B1 结果 | 同一次已存在的 Shadow 报告 | 需 `b1: true`；只记录实际观察的授权候选及 cap，未运行/失败/部分覆盖分别保留；不会为 benchmark 额外调用 AI | 是否允许将该源获准字段发送现有 Shadow 模型；其服务条款及跨境数据处理 |
| `language` | RSS 条目语言 → RSS feed 语言 → `source.metadata.language` → `unknown` | 沿用现有 Collector；不把缺失语言猜成其他值 | 各源语言声明是否可靠 |
| `epub_language` | 来源 `source.metadata.epub_language`、文章 metadata/quality | 这是 EPUB 文字语言的独立回退，不是 Collector 的 `metadata.language`；此功能不把它当 RSS 语言 | 如需语言映射，须单独评估并另行授权配置 |

源权限配置保存在新 Secret `EDITORIAL_BENCHMARK_PERMISSIONS`，格式为 `{ "<source-id>": {"title": true, "excerpt": false, "b1": false} }`；三项均为布尔值。`title: true` 同时授权表中来源标签、候选 ID、时间、媒体提示及 B0 结果的私有采集；若其中任何一项未获许可，则不要配置该来源。source-id 是 `sha256(UTF-8("rss:" + RSS源完整URL))` 的小写十六进制，与 `DedupTracker.make_source_key` 的来源键一致。它不是匿名化保证（可字典猜测），不要公开该表或 ID。URL 必须与现有配置中精确一致；同一 URL 的多条配置共用权限。缺少或无效权限会禁用采集，不会默认全源授权。要在本机私下计算，可使用 `python -c 'import hashlib; print(hashlib.sha256(("rss:"+input("RSS URL: ")).encode()).hexdigest())'`；不要将结果贴到公开日志或 PR。

启用 benchmark 后，Shadow 原有输入将按此表的 `b1` 过滤，且 `excerpt: false` 的来源摘要不会发往模型；权限无效时 Shadow 输入为空而非回退至全部来源。这会改变启用期间的 Shadow 候选，但不改变正式选稿。`b1` 不授权时仍可采集该源 B0。B1 报告中的分组可能受当时其他经授权来源影响；cap 后未观察条目不补为单例。B1 停用为 `not_run`、失败为 `failed`、截断/授权子集为 `partial`；对齐异常失败关闭，不伪造选择。

已有 Shadow 工作流仍会在 Actions 临时运行器上创建权限受限的 `output/.ai-candidates.json` 明文临时文件，并在 Shadow 步骤后删除；benchmark 自身的跨步骤暂存始终加密。被中止的运行器或现有日志、报告及其他工作流的留存策略仍需仓库所有者审阅。若不接受这种已有明文暂存，应保持 Shadow 关闭，或先单独改造并验证该路径。

## 实际时间与数据边界

每个候选记录真实 `collected_at`，同时记录实际收集窗口起止以及本次出版日的上海 06:30 目标时间。`captured_at` 是该窗口结束的实际时刻，不是计划 06:30：如果采集发生在目标时间之后，不能把这些信息用于评价 06:30 时本可做出的决定。来源并行抓取，B0 各源的决策可能发生在完整窗口结束之前；公平比较应分别审查实际可观察时间，不能把完整窗口统一冒充 B0 早期输入。固定输入之外的 B0 正文质量只在该次 B0 结果中，B1/B2 不读取它。仅记录 RSS 经过历史去重后的候选，并非完整 RSS 或所有新闻。

## 用户启用步骤（等待审批及依赖合并）

1. 确认来源、标题、摘要、第三方 AI 及私人存储授权；构建逐源权限表。将它作为 GitHub Secret `EDITORIAL_BENCHMARK_PERMISSIONS`，不要改生产 `CONFIG_JSON`。未明确授权来源不得配置为 true。
2. 在坚果云网页端预先创建独立目录 `OughtGatherBenchmark`；不能与正式 `DailyWorldBriefing` 或 `OughtGatherState` 重叠。沿用既有 HTTPS WebDAV 凭据。新建独立随机 32 字节密钥并作为 base64 放入 GitHub Secret `EDITORIAL_BENCHMARK_KEY`，不要复用 Editorial Memory 或备份密钥。丢失密钥将无法恢复；应私下安全备份。
3. 检查并批准工作流隐私取舍：启用时主抓取步骤的公开控制台输出被抑制，AI/质量报告、日志、dedup 明文 Artifact 和手动明文 EPUB Artifact 停止上传；加密出版补传及出版健康状态仍保留。**历史日志和 Artifact 不会被删除**，平台及其他已有工作流的可见性仍须由仓库所有者检查。测试模式不会采集或投递 benchmark。
4. 经用户授权后才设置 GitHub Variable `EDITORIAL_BENCHMARK_ENABLED` 为精确小写 `true`；可设置 `EDITORIAL_BENCHMARK_RETENTION_DAYS`（默认 30，1–3650）。这是用户操作，本 PR 不修改任何 Secret/Variable。需要取消时移除该 Variable 或设为 `false`，不影响正式出版。
5. 在用户允许的计划运行后，检查安全状态消息：`saved`、`already frozen`、`saved; retention cleanup unavailable` 或 `unavailable`。内容、原始异常、URL、密钥不写入该日志。存储故障不会中止正式出版；若加密或认证失败不会生成明文回退。

## 加密、冻结与保留

独立版本头 `OGBENCH\x01`、随机 salt/nonce、HKDF-SHA256 上下文 `oughtgather-benchmark-v1` 及 AES-256-GCM；不修改 Editorial Memory 或 EPUB 备份格式。采集数据先仅以密文暂存，Shadow 完成后在内存中加入结果，再用独立密钥加密并用临时 PUT/GET 校验、`MOVE Overwrite: F`、GET 校验冻结为 `YYYY-MM-DD.enc`。目标已存在则拒绝并明确报告，不静默覆盖；成功保存后按保留天数删除匹配、位于本目录中的旧归档。删除失败只报告安全类别，不删除当前快照。并发下不覆盖依赖服务端正确执行 WebDAV `Overwrite: F`；离线模拟不是坚果云实测证明，应在用户授权的隔离目录单独验证，不能在此 PR 自动访问真实服务。备份及服务端历史版本可能超出本工具保留策略。

## 本地读取、盲评和 replay

操作在用户自己的受限环境中进行；先安全设置既有 WebDAV 凭据和 `EDITORIAL_BENCHMARK_KEY`，命令本身不要写入真实密钥。创建仓库外、仅用户可访问的 `0700` 目录，例如本地 `/tmp/private-review`，不要同步或提交该目录。下载只保存密文：

```sh
python -m src.editorial_benchmark.download 2026-11-24 /tmp/private-review/snapshot.enc
python -m src.editorial_benchmark.review create /tmp/private-review/snapshot.enc /tmp/private-review --sample 5 --b2
# 在本地编辑 review.json；不要上传该文件
python -m src.editorial_benchmark.review apply /tmp/private-review/snapshot.enc /tmp/private-review /tmp/private-review/labeled.enc
python -m scripts.editorial_v2.replay /tmp/private-review/labeled.enc --encrypted-private --b2
```

`review.json` 只呈现打乱后的代号、标题、来源和日期，合并各算法入选项目，可额外随机抽样未入选项；不展示算法名称。`--b2` 只运行本地 B2。逐条填写 `event_id` 和 `importance`（`important`、`not_important`、`unknown`、`disputed`）；`incremental_against` 使用先前代号为键，值为 `substantive_update`、`new_information`、`no_new_information`、`unknown` 或 `disputed`。未知重要性但已知同事件可填写事件 ID；争议保留，不伪造成判定。评测结果只在本地安全终端查看；聚合小样本也可能反向识别，不能默认公开。工具仅在本地写 `0600` 的 review 文件和加密映射/结果；明文 review 由用户负责安全删除。
