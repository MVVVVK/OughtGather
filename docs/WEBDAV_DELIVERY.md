# WebDAV 每日投递与安全补传

## 工作方式

Daily Gather 定时为 UTC 22:30，即北京时间每天 06:30。GitHub Actions 可能因排队延迟；它不是实时闹钟。定时运行不再因之前 18 小时内存在外部触发而跳过。外部触发、Fresh Start 和补传共用原有并发组；RSS 仍遵循 PR #3 的出版去重规则。**建议只启用一个每日定时触发器**：GitHub Actions 或 Cloudflare Worker 二选一。若启用 Worker，请从 `daily-gather.yml` 删除或注释 `on.schedule`（保留工作流启用及 `repository_dispatch`）；若使用 Actions 定时，请在 Cloudflare 禁用 Worker 的 Cron Trigger。两者同时启用可能重复运行，两次之间的新文章仍可能生成新一期。仓库中的 Cloudflare Worker 配置也改为 06:30；已经部署的 Worker 需要重新部署或手动调整，才能改变其现有定时。

启用 WebDAV 后，先用独立密钥加密完整 EPUB 形成补传包，再尝试 WebDAV。正式远端目录（例如 `DailyWorldBriefing`）建议先在坚果云网页创建。上传器先以 PROPFIND 检查目录，仅在明确收到 404 时才尝试创建其内部缺失目录（包括 `archive`）；400、403、405 不会被无条件视为成功。远端目录下的 `archive/` 按北京时间生成带时间和 SHA-256 的文件名；原样补传使用同一文件名。成功上传后读取远端文件逐字节校验。`latest.epub` 先上传到同目录临时文件并校验，再通过 WebDAV `MOVE` 替换；覆盖前会保存并验证 `latest.previous.epub`。较旧期次的补传不会覆盖已有的较新期次。默认保留最近 30 个日历日的归档，仅删除本程序命名格式且超过期限的归档；`latest.epub`、`latest.previous.epub` 和其他文件不会被归档清理删除。清理失败记在投递状态中，不覆盖成功的投递结果。

这些保证依赖 WebDAV 服务端正确实现 `MKCOL`、`PROPFIND`、`GET`、`PUT` 和支持覆盖的原子 `MOVE`。网络断开可能发生在服务器处理请求之后、客户端收到响应之前；此时结果可能不确定，应使用同一个补传包重试。替换后的校验失败时程序会尝试恢复上期；服务器或其他客户端并发修改、非原子 MOVE、服务端损坏或故障期间无法恢复时，无法承诺分布式事务，`latest.previous.epub` 仍保留上一期的额外副本。请先用个人坚果云账户验证操作与权限。

## 坚果云和 GitHub Secrets

在坚果云账户设置中启用第三方应用访问并创建应用密码；使用账号和应用密码，不要将密码提交到仓库。在仓库 **Settings → Secrets and variables → Actions** 配置：

| Secret | 用途 |
| --- | --- |
| `WEBDAV_ENABLED` | `true` 启用 WebDAV |
| `WEBDAV_URL` | 坚果云 WebDAV 根地址，例如 `https://dav.jianguoyun.com/dav/` |
| `WEBDAV_USERNAME` | 坚果云登录账号 |
| `WEBDAV_PASSWORD` | 坚果云第三方应用密码 |
| `WEBDAV_REMOTE_PATH` | 根目录下的相对目录，例如 `OughtGather`；程序按需创建 |
| `WEBDAV_RETENTION_DAYS` | 可选，1–3650；空值默认 30 |
| `DELIVERY_BACKUP_KEY` | 与 WebDAV 密码不同的高强度随机密钥，至少 32 字节；启用 WebDAV 时必需 |

不要把密钥写进 `config.json`、命令行或日志。请在密码管理器中生成并保存足够随机的独立密钥，再通过 GitHub Secrets 界面设置；修改或丢失密钥后，旧加密备份不能解密。因为补传包需用此密钥解密，**迁移到该版本前先设置密钥**；缺少或过短时普通 WebDAV 日报会在抓取和更改去重历史之前停止。使用 HTTPS 保护账号凭据。

邮件可选。沿用 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`；新增通用 `EMAIL_RECIPIENT`，未设置时兼容旧 `KINDLE_EMAIL`。WebDAV 失败默认发送无附件通知；设置 `EMAIL_FALLBACK_ATTACHMENT=true` 后失败通知改为包含同一 EPUB 的备用邮件。关闭 WebDAV 时保留原有邮件附件投递。邮件失败不会被误报为 WebDAV 成功。

## Ocean 6 Pro

在阅读器内置 WebDAV 中配置相同的坚果云账户，进入上面设置的目录，选择 `latest.epub` 下载并导入；历史期次位于 `archive/`，上一个 latest 的备份为 `latest.previous.epub`。阅读器具体菜单名称可能随固件变化；本项目无需安装额外同步应用。

## 状态、失败与补传

每次启用 WebDAV 的投递在 `delivery-status` artifact 中记录状态、错误类别、归档标识及邮件结果。类别包括 `authentication`、`network`、`quota`、`verification`、`unsupported`、`remote`、`redirect` 和 `configuration`；不支持的操作及 HTTP 状态显示在 `error_detail` 或 `cleanup_warning` 中。检查该状态及工作流日志；认证失败请检查应用密码和目录权限，网络失败可稍后重试，`verification` 或 `remote` 请核对服务端 WebDAV 能力。`cleanup_error` 表示归档清理失败，最新一期可能已经成功送达。

失败时，Actions 保存 `delivery-retry-<run_id>-<attempt>` artifact，内含经 AES-256-GCM 认证加密的 EPUB 和期次信息，密钥经 PBKDF2-HMAC-SHA256 派生；保留 30 天，过期后不能从 Actions 恢复。失败通知邮件不会包含密钥。密文仍应视为敏感资料，避免泄露密钥。该备份是短期恢复手段，不是永久备份；EPUB 已生成并按 PR #3 保存去重记录后，若加密包创建因磁盘错误失败、Actions runner 在上传 artifact 前被终止，或 GitHub artifact 服务不可用，也可能无法保存补传包。用户可另行维护私人备份。

补传步骤：

1. 在失败的 Daily Gather 运行页面记下 run ID 和 attempt 数，并确认该运行已有 `delivery-retry-...` artifact。
2. 修复 WebDAV 网络或认证问题，保持 `DELIVERY_BACKUP_KEY` 与原运行一致。
3. 手动运行 **Retry WebDAV Delivery**，填写原 run ID 和 attempt。它只下载和解密该备份并重传同一期，不读取、更新或重置 RSS 去重记录，也不会重新抓新闻。
4. 检查 `delivery-retry-status` 和远端文件；如失败，可再次使用同一加密包重试。不要在补传工作流中修改密钥或原始 artifact。

本地补传可先将密文放到受保护的本地文件，再通过环境变量安全提供密钥与 WebDAV 配置，运行 `python -m src.delivery --encrypted /path/to/delivery-retry.enc`。不要将密钥直接写进 shell 命令历史。

`test_mode` 不上传 WebDAV、不发邮件、不生成补传包或修改正式去重缓存。`epub-files` 明文 artifact 默认关闭；只有手动 Daily Gather 明确勾选 `upload_plaintext_epub` 才会上传，保留 7 天。可访问该 artifact 的人可能看到内容，勿把它用于私人 EPUB 的永久备份。质量报告也包含来源和文章 URL，分享前应检查是否有敏感参数。


## 独立的坚果云冒烟测试

这是单独的测试程序，不会抓取 RSS、读取或修改去重记录、读取 `WEBDAV_REMOTE_PATH` 或访问 `latest.epub`。程序只接受 HTTPS 的 `https://dav.jianguoyun.com/dav/` 根地址；用户需先通过坚果云网页在根目录创建专用文件夹 `OughtGatherSmoke`。程序先通过 PROPFIND 确认它是可访问的文件夹，然后仅在其中创建 `t-<12位随机十六进制>` 临时子目录，用固定测试字节验证 MKCOL、PUT、GET、PROPFIND、MOVE（新建及覆盖）和 DELETE；只清理本次子目录，不删除预建文件夹。不会进入正式 `DailyWorldBriefing` 目录。用户应先确认使用的坚果云账户和根目录，明确授权后才运行。

手动验证步骤：

1. 在坚果云网页的根目录预先创建 `OughtGatherSmoke`。它不是正式的 `DailyWorldBriefing` 文件夹。
2. 在 GitHub Actions 选择 **Isolated WebDAV Smoke Test → Run workflow**。本修复合并前，请明确选择修复分支 `codex/jianguoyun-dav-compat`；不要选择仍含旧测试的 `main`。合并后可选择 `main`。
3. 确认账户和路径后，明确勾选 `authorize` 并运行。它只使用已有 `WEBDAV_URL`、`WEBDAV_USERNAME`、`WEBDAV_PASSWORD` Secrets，不使用正式目录配置或其他 Secrets。
4. 查看运行日志中的操作状态及 `residual_path`；如果父文件夹不存在，先在网页创建后再手动运行。

也可在检出修复分支后本地验证：通过安全的环境变量机制提供这三个值，再运行 `python -m src.uploader.webdav_smoke --authorize-isolated`。不要把密码写进命令行或终端历史。

报告只显示操作及其 HTTP 状态、失败原因、可能残留的随机子目录路径与清理结果，不显示认证信息、URL 或服务端响应正文。HTTP 405 表示操作不允许或不支持（MKCOL 也可能是同名目录已存在），501 表示不支持；失败后只尝试清理本次成功创建的测试目录和固定测试文件。`residual_path` 非空表示可能有残留：如果 `cleanup_errors` 非空，请检查 `OughtGatherSmoke/t-...` 并仅在确认属于本次测试时移除该子目录；如果 MKCOL 未确认成功，程序不会自动删除可能已存在的目录，需人工确认。不要删除 `OughtGatherSmoke` 或操作正式新闻目录。服务端不支持所需操作时不要启用正式投递。
