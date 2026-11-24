# B3 语义路线：实现与实验阻塞

## 当前状态

[真实 CPU E5 冒烟测试 run 45345874515](https://github.com/MVVVVK/OughtGather/actions/runs/45345874515) 的 job 已成功。用户提供的 Artifact 数值保存在 [聚合记录](data/e5_smoke_45345874515.json)：模型文件 492,794,646 bytes，下载 3.467 秒、加载 3.084 秒、四标题推理 0.032 秒，峰值 RSS 888,968 KiB；虚构同事件跨语言对平均余弦 0.926006，不同事件对平均 0.839302。仅四条标题不能确定阈值、事件准确率或生产效果；本会话核对了 job 成功状态，数值来自用户提供的 Artifact 摘要，未独立读取 Artifact 内容。

[官方模型卡](https://huggingface.co/intfloat/multilingual-e5-small) 标注 MIT 许可；固定 revision `fd1525a9fd15316a2d503bf26ab031a61d056e98`，12 层、384 维。编码遵循模型卡：每条前缀 `query: `、512 token 截断、attention-mask 均值池化、L2 归一化，余弦相似度。`E5Encoder` 默认 `local_files_only=True`，不会自动联网下载。

## 已实现的可替换接口

- `semantic.e5.E5Encoder` 使用隔离实验依赖的真实 Transformers/PyTorch CPU 实现和固定 revision；依赖仅列入 `requirements-b3-experiment.txt`，不加入生产依赖。必须先在用户许可的网络中预取同一 revision，不能把权重放入 Git。安装时应选官方与系统兼容的 **CPU** PyTorch wheel，再安装文件中其余依赖。首次安装、模型许可和文件完整性由实验运行者验证。
- `semantic.relations` 用真实向量余弦做候选召回，并复用既有启发式实体/动作证据，不添加手工词表。时间可为 compatible、conflict 或 unknown；实体、动作与时间仅供复核参考，不单独决定合并或阻止复核。没有获准的明确复核时保守保留；完全链接约束要求组内每对均可合并。语义相似、同主题或同主体不能单独证明同事件，缺乏已校准阈值时不可用于生产。阈值必须由调用方显式传入，不能由验收集调参。
- `semantic.deepseek.DeepSeekEditorial` 适配现有 Responses 客户端与 `Budget`，提供关系复核和 A/B/C/D 重要性接口；默认不能调用，须显式 `allow_external=True`，并逐候选及逐事实提供许可集合。输入只含获准标题/摘要和简短 ID；输出严格验证等级、候选证据 ID、事实 ID 和不确定性，不使用 tools、thinking 或原始输出作为结果。mock transport 测试没有调用真实 API；未获得授权不得发送私人来源或阅读记录。
- `semantic.editor.run` 注入 encoder、重要性评估器及正文验证器；默认最多 40 次验证器调用，失败尝试下一篇，无法验证则可少于 16 篇。预算限制的是**接口调用次数**；验证器必须自行约束其内部 HTTP 请求、时间和访问权限，不能绕过付费墙或验证码。未知/高不确定性不伪装为重要性已确认。选稿基于主题级事实估计边际价值；只有代表文章验证器显式确认的事实 ID 才计入已覆盖，未确认事实单独列出；高不确定性和验证失败事件进入待复核清单。事实 ID 它们必须在选稿前独立建立并获授权，不能用 gold 或 B0 后验结果泄漏。类别关键词不再驱动该选择。

这些接口不接入正式选稿，实验必须由调用者显式传入评估器、验证器和阈值。DeepSeek 真实重要性实验与本地 E5 语义实验分别统计；DeepSeek 重要性接口仅有离线模拟验证，没有真实调用结果。

## 真实新闻数据实验的边界（未运行）

`scripts.editorial_v2.semantic.crossnews` 接受与既有实验相同的 CrossNews-UA CSV 并核对固定 SHA-256；不下载数据、不输出正文或 URL。模型及依赖准备就绪后可在私有实验环境运行：

```sh
python -m scripts.editorial_v2.semantic.crossnews /local/path/crossnews-ua.csv
```

预先固定 `similar`/`different` 代理标签，排除不确定标签；将全部文章 URL 连通分量通过固定哈希分为开发和探索性留出，保证精确 URL 不跨集合。开发集在预先声明的 0.50–0.95 网格选择 F1 阈值（相同值取较高阈值），留出集只评一次；报告精确率、召回率、F1、平均精度形式的 PR-AUC、分母、跨语言分组、无日期及 `somewhat_related` 匹配情况、辅助证据下的弃判率和不含正文的错误行号。**该数据没有可靠事件 ID**：不同 URL 的同一事件仍可能跨集合，故此留出集只是探索集，不能作为独立事件验收集。`somewhat_related` 不等同已证实的不同事件；人手审核后才可作为严格困难负例。标签本身是主题/事件相似的代理，而非严格事件等价；缺失发布时间不应被伪造。

本地离线检查仅验证了分组：500 对中开发 414、留出 86；跨语言且 `similar`/`different` 开发 172（12 正、160 负），留出 41（2 正、39 负）。它不包含任何模型得分、已选阈值、PR-AUC 或验收结论。样本小且同源局限大；不得根据留出结果反复调整阈值。重要性、边际新增事实及代表文章质量没有 gold，不能通过这个数据集评估。

## 补充数据与停止条件

[SemEval-2022 Task 8](https://zenodo.org/records/6507872) 提供 URL 对及相似度标注，记录页面的 license 字段未清楚显示许可证；原文章在第三方档案，不能由标注开放推定原文获准复制。CrossNews-UA 部分采样自该任务，若混用还需检查同文章/事件泄漏。[Priberam news-clustering](https://github.com/Priberam/news-clustering) 仓库提供代码及 FTP 下载脚本，但代码许可证不自动授予新闻原文或数据许可；未找到足以确认该数据可在本项目使用的明确条款，因此未下载或复制。两者目前都不能当作已获许可的独立验收集。

需要用户在符合来源许可的环境准备可访问的固定 E5 缓存、明确可用的独立事件标注数据，以及必要的成本和隐私授权。先运行开发集，再以文章**及事件**无交叉的独立集合单次评估；报告语义原始分数与保守复核后的表现、相近但不同事件、缺日期、错误案例和 CPU 开销。无这些证据或重要性/质量标签前，停止声称模型优于 B2/B3-heuristic，也不得部署正式选稿。

## 手动 GitHub-hosted CPU 冒烟测试

PR #13 的现有 **CI** 工作流增加 `b3-e5-experiment` job，只在手动 `workflow_dispatch` 且分支为 `codex/editorial-b3-hybrid` 时执行；普通 PR 和 push 不下载模型。job 使用 GitHub-hosted Ubuntu、Python 3.11、官方 CPU PyTorch、`contents: read`、30 分钟上限，不引用生产 Secrets、不运行日报、DeepSeek 或 WebDAV。它只从 Hugging Face 官方仓库获取固定 revision 的 safetensors、模型配置及 tokenizer 必需文件，不获取重复权重；不保存模型为 Artifact。

在 GitHub Actions 页面选择 **CI** → **Run workflow**，分支选择 **`codex/editorial-b3-hybrid`** 并运行。检查 `b3-e5-experiment` job，下载 `b3-e5-aggregate` Artifact 中的 `b3-e5-report.json`；该文件仅包含阶段状态和聚合指标。`failure_stage=dependencies`、`model_download`、`model_load` 或 `synthetic_inference` 指出失败阶段；若官方返回 HTTP 403，记录状态后停止，不使用镜像或绕过限制。成功报告记录实际下载文件字节总数、加载及四条自行编写英法标题的推理时间、384 维向量范数范围和进程峰值 RSS。合成句子的相似度均值只验证真实推理，不是新闻事件准确率。

CrossNews-UA 数据卡声明 CC BY 4.0，但 CSV 包含第三方新闻全文，原文可否由本项目进行处理尚未独立确认。因此该 job **不下载或处理 CrossNews-UA**，在报告中标为 `skipped_license_unconfirmed`。获得明确许可并完成原文权限核验前，不运行数据实验；当前没有真实模型的 precision、recall、F1、Average Precision、弃判率或失败案例。此前的 CrossNews-UA 规则结果与代码入口仍保留，不将合成句子当作模型在新闻数据上的评测。


## 100 对合成验证与 300 候选性能测试

新增 [100 对自行编写的完全虚构标题](../tests/fixtures/editorial_v2/synthetic_event_pairs_100.json)，不含真实新闻或第三方文本；覆盖英法同事件、同一主体不同事件、实质更新、相近但不同事件及缺失时间。固定 SHA-256 `5505ad7f214ea70155afeb8c8a2a39353e99894985abae911c059c16e65ffd6c`；50 对开发、50 对验收按虚构事件分离，程序校验事件和标题无交叉。相同事件及其实质更新算事件正例，但合并成功不代表选中的文章包含所有新增事实。

现有手动 CI 会先对开发集从预注册的 0.50–0.95 网格按 F1 选阈值（同分取高值），再仅用该阈值计算冻结验收集的 B2、B3-heuristic、原始 E5 和辅助证据混合方法；报告每项 TP、FP、FN、TN、precision、recall、F1、Average Precision、弃判、无日期、同主体不同事件、相关不同事件及更新子集。不能据验收集结果反复调参。

随后使用**同一真实 E5 实例**运行 300 条虚构候选的端到端选稿，记录 CPU 耗时、进程峰值内存、观察数量、聚类规模、最多 16 篇、验证次数上限及备用文章情况。重要性和正文验证器为预设合成回调，绝非真实 DeepSeek 或正文抓取；这验证控制流，不证明内容判断。该次实验的旧版算法使用组级事实 ID，不能证明最终代表文章包含所有声明事实；后续修复仅以离线测试验证，不能追溯改变该次实验结果。

### 冻结实验及最小试运行

[真实运行 45348237659](https://github.com/MVVVVK/OughtGather/actions/runs/45348237659) 的用户提供摘要已保存为 [聚合记录](data/e5_synthetic_45348237659.json)。Artifact 下载在本环境返回 403，故记录明确注明来源并未冒称独立读取原始报告。50 对自编冻结集：B2 与 B3 heuristic F1 均为 0；E5 raw F1 0.935484（TP 29、FP 3、FN 1、TN 17）；旧 hybrid F1 0、50 对弃判；阈值 0.88。300 条合成候选耗时 6.534 秒，峰值 RSS 1,119,980 KiB。这些是旧版本的合成数据结果，不能当作新规则或真实新闻表现的证据；不据此重调冻结阈值。

最小试运行仅对少量**自行撰写的事实描述**，只读本地文件、只用已缓存的 E5，不访问网络、Benchmark、正式日报或外部 AI。需要自行安装隔离实验依赖并按前文预取固定 revision；入口不会下载模型。先在私有位置准备 JSON（不要提交到 Git）：

```json
{"content_origin":"self_authored", "captured_at":"2026-11-24T06:30:00+08:00",
 "candidates":[{"candidate_id":"a", "title":"自己撰写的事件事实"},
               {"candidate_id":"b", "title":"自己撰写的直接后续"}],
 "manual_reviews":[{"a":"a", "b":"b", "relation":"substantive_update"}]}
```

```bash
python -m scripts.editorial_v2.semantic.trial --enable --input /private/path/trial.json
```

最多 20 条；可选 `published_at`，应带时区。人工复核允许 `same_event`、`substantive_update`、`related_different`、`unknown`；E5 召回不到或未复核时不合并。0.88 仅沿用合成开发集阈值，不能视为真实事件校准。输出是组 ID 列表，非出版选稿或事实核验；不要将私有输入或输出上传公开 Artifact。实际媒体内容需单独确认来源许可和外部 AI 授权，本入口仅接受自述为自行撰写的内容，不能替代法律许可核查。

上线仍受限于真实授权新闻上的独立事件标注、误合并/遗漏验证、人工语义复核容量、文章级事实与正文质量验证。此次人工复核和测试 double 不能证明模型或编辑质量；不建议自动接入正式出版。
