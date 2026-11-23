# 去重与重试

## RSS 的生命周期

RSS 每次先发现 feed 条目，再按标题、已有历史、失败冷却和配置选出候选，之后抓取正文并进行质量检查与内容处理。发现、进入候选、尝试抓取、通过质量检查和实际出版是不同阶段。

正常运行中，RSS **仅在 EPUB 生成成功之后**将实际收入 EPUB 且有合格正文或摘要的文章加入出版历史。配额未处理、标题或媒体过滤、正文失败的文章不会因为出现在 feed 中而进入出版历史；生成 EPUB 失败也不会提交出版记录。只有元信息的失败条目可以收入一次 EPUB，但仍保留受限重试的机会，后续失败时不会重复刊登元信息。成功获得正文或摘要后再次收入 EPUB 并记录出版。非 RSS 抓取器继续使用原有快照机制。

失败文章在同一 source 下最多自动尝试 3 次，两次间隔至少 24 小时，超过次数后不再自动请求。手动运行 Daily Gather 时勾选 `retry_failed`，或本地使用 `python src/main.py --retry-failed`，可只重置失败计数，不删除已出版历史。配额未处理及策略过滤不消耗失败次数。`test_mode` 始终使用临时独立文件，不影响正式历史和失败计数。

## 文件与旧缓存

继续使用 `data/fetched_urls.txt` 和原有 Actions 缓存键及迁移路径。原格式每行是 `{source.type}:{source.src}`、Tab、URL hash；新版本可直接读取。新写入的重试行是 `@retry`、source key、URL hash、尝试次数、UTC epoch（各字段用 Tab 分隔），不含原文 URL；这些行与已出版记录一起原子保存。旧版程序不能读取重试行，回退旧版前应先备份并移除这些行。

旧版 RSS 快照只记录已发现的 URL，不能区分哪些曾出版、哪些只是因配额跳过。升级时保守地将旧记录都视为已处理，避免重复出版，因此不能自动恢复旧版本已误标的未处理文章；需在了解重复风险后使用 `test_mode` 检查或有意识地执行 Fresh Start。新规则不会自动清空旧记录。

## Fresh Start

手动 Fresh Start 特意把当前各去重来源的全部可见 URL 作为新基线，且不生成或发送 EPUB。所有来源成功后才原子替换记录并清除失败计数；任何来源失败保留原文件。即使新基线为空，也会成功写入空文件。Fresh Start 是显式初始化，与普通运行不同。

## Actions 缓存

Daily Gather 与 Fresh Start 共用串行组，使用 `dedup-v2-<run_id>-<run_attempt>` 不可变键；Daily 通过前缀恢复最新创建的 v2 快照，成功后显式保存新键，首次迁移时回退到旧 `dedup-` 前缀。重复运行同一 workflow 时 `run_attempt` 会变化。缓存可能被 GitHub 淘汰；全部被淘汰后只能重新建立历史。

GitHub 缓存受分支作用域限制：正式 Daily Gather 和 Fresh Start 应在默认分支运行。`test_mode` 使用空的临时去重文件，不保存 Actions 去重缓存，不发送邮件或 WebDAV。可通过测试配置中的 `metadata.limit` 或全局 `limit` 限制测试规模。
