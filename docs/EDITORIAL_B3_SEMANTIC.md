# B3 语义路线：实现与实验阻塞

## 当前状态

**尚未完成真实模型实验，不可据此宣称 B3 改善或通过验收。** 2026-11-24，本环境对官方 Hugging Face 模型 `config.json` 请求返回 HTTP 403，环境没有 E5 权重缓存，也没有 PyTorch/Transformers；没有绕过访问限制。原来的 [B3-heuristic 与 CrossNews-UA 结果](EDITORIAL_B3_EXPERIMENT.md) 保留：原 213 个跨语言代理标签对全部弃判，F1 0。此 PR 的测试替身仅检验代码契约，并非模型运行结果。

[官方模型卡](https://huggingface.co/intfloat/multilingual-e5-small) 标注 MIT 许可；固定 revision `fd1525a9fd15316a2d503bf26ab031a61d056e98`，12 层、384 维。官方文件列表标注 safetensors 约 471 MB，tokenizer 文件等需额外空间；这是远端标称，不是本机已下载体积。实际 CPU 推理时间、内存、模型性能及失败样本目前**未知**。编码遵循模型卡：每条前缀 `query: `、512 token 截断、attention-mask 均值池化、L2 归一化，余弦相似度。`E5Encoder` 默认 `local_files_only=True`，不会自动联网下载。

## 已实现的可替换接口

- `semantic.e5.E5Encoder` 使用隔离实验依赖的真实 Transformers/PyTorch CPU 实现和固定 revision；依赖仅列入 `requirements-b3-experiment.txt`，不加入生产依赖。必须先在用户许可的网络中预取同一 revision，不能把权重放入 Git。安装时应选官方与系统兼容的 **CPU** PyTorch wheel，再安装文件中其余依赖。首次安装、模型许可和文件完整性由实验运行者验证。
- `semantic.relations` 用真实向量余弦做候选召回，并复用既有启发式实体/动作证据，不添加手工词表。时间可为 compatible、conflict 或 unknown；缺失日期不是一票否决，明确时间冲突不自动合并。没有证据时弃判，可显式注入复核器；完全链接约束要求组内每对均可合并。语义相似、同主题或同主体不能单独证明同事件，缺乏已校准阈值时不可用于生产。阈值必须由调用方显式传入，不能由验收集调参。
- `semantic.deepseek.DeepSeekEditorial` 适配现有 Responses 客户端与 `Budget`，提供关系复核和 A/B/C/D 重要性接口；默认不能调用，须显式 `allow_external=True`，并逐候选及逐事实提供许可集合。输入只含获准标题/摘要和简短 ID；输出严格验证等级、候选证据 ID、事实 ID 和不确定性，不使用 tools、thinking 或原始输出作为结果。mock transport 测试没有调用真实 API；未获得授权不得发送私人来源或阅读记录。
- `semantic.editor.run` 注入 encoder、重要性评估器及正文验证器；默认最多 40 次验证器调用，失败尝试下一篇，无法验证则可少于 16 篇。预算限制的是**接口调用次数**；验证器必须自行约束其内部 HTTP 请求、时间和访问权限，不能绕过付费墙或验证码。未知/高不确定性不伪装为重要性已确认。已提供的事实 ID 可参与未覆盖事实的边际选择；它们必须在选稿前独立建立并获授权，不能用 gold 或 B0 后验结果泄漏。类别关键词不再驱动该选择。

这些接口不接入正式选稿，实验必须由调用者显式传入评估器、验证器和阈值。DeepSeek 真实重要性实验与本地 E5 语义实验分别统计；目前两者均**没有真实调用结果**。

## 已准备但尚未运行的真实数据实验

`scripts.editorial_v2.semantic.crossnews` 接受与既有实验相同的 CrossNews-UA CSV 并核对固定 SHA-256；不下载数据、不输出正文或 URL。模型及依赖准备就绪后可在私有实验环境运行：

```sh
python -m scripts.editorial_v2.semantic.crossnews /local/path/crossnews-ua.csv
```

预先固定 `similar`/`different` 代理标签，排除不确定标签；将全部文章 URL 连通分量通过固定哈希分为开发和探索性留出，保证精确 URL 不跨集合。开发集在预先声明的 0.50–0.95 网格选择 F1 阈值（相同值取较高阈值），留出集只评一次；报告精确率、召回率、F1、平均精度形式的 PR-AUC、分母、跨语言分组、无日期及 `somewhat_related` 匹配情况、辅助证据下的弃判率和不含正文的错误行号。**该数据没有可靠事件 ID**：不同 URL 的同一事件仍可能跨集合，故此留出集只是探索集，不能作为独立事件验收集。`somewhat_related` 不等同已证实的不同事件；人手审核后才可作为严格困难负例。标签本身是主题/事件相似的代理，而非严格事件等价；缺失发布时间不应被伪造。

本地离线检查仅验证了分组：500 对中开发 414、留出 86；跨语言且 `similar`/`different` 开发 172（12 正、160 负），留出 41（2 正、39 负）。它不包含任何模型得分、已选阈值、PR-AUC 或验收结论。样本小且同源局限大；不得根据留出结果反复调整阈值。重要性、边际新增事实及代表文章质量没有 gold，不能通过这个数据集评估。

## 补充数据与停止条件

[SemEval-2022 Task 8](https://zenodo.org/records/6507872) 提供 URL 对及相似度标注，记录页面的 license 字段未清楚显示许可证；原文章在第三方档案，不能由标注开放推定原文获准复制。CrossNews-UA 部分采样自该任务，若混用还需检查同文章/事件泄漏。[Priberam news-clustering](https://github.com/Priberam/news-clustering) 仓库提供代码及 FTP 下载脚本，但代码许可证不自动授予新闻原文或数据许可；未找到足以确认该数据可在本项目使用的明确条款，因此未下载或复制。两者目前都不能当作已获许可的独立验收集。

需要用户在符合来源许可的环境准备可访问的固定 E5 缓存、明确可用的独立事件标注数据，以及必要的成本和隐私授权。先运行开发集，再以文章**及事件**无交叉的独立集合单次评估；报告语义原始分数与保守复核后的表现、相近但不同事件、缺日期、错误案例和 CPU 开销。无这些证据或重要性/质量标签前，停止声称模型优于 B2/B3-heuristic，也不得部署正式选稿。
