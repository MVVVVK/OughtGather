# Ocean 6 Pro 法语字体对照

| 文件 | 字体方案 | 体积 |
| --- | --- | ---: |
| [A · Baseline](A-baseline.epub) | 现有 CSS 字体配置，由阅读器选择字体 | 22,745 B |
| [B · Font Fix Candidate](B-font-fix-candidate.epub) | 法语区域优先使用四个静态 Noto Serif 拉丁子集 | 423,875 B |

B 增加 401,130 B（约 392 KiB）。两书使用完全相同的合成内容、XHTML、章节顺序、语言标记和布局规则。A 使用原有字体配置，两书都应用 CSS 链接修复，便于孤立比较字体。两书使用独立的 EPUB 标识符，书名相同。请分批导入以便辨认。

## 已确认的生成问题

以 `760b1c03af1090b877a77ebf77de038d4d3fa0d8` 的生成器和 EbookLib 0.20 另行生成同一素材。成品的正文与栏目分隔页没有样式链接，导航与导读有链接。EbookLib 会重新生成 XHTML 的根元素和 head，写进原始 HTML 的链接和根语言属性会被替换。项目原有的正文 CSS 也没有拉丁字体声明，OPF 和章节根语言沿用界面语言。该版本成品的法语 NFC、NFD 测试文本均可从 UTF-8 XHTML 读取。

因此原始 EPUB 将正文选字和缺字回退交给阅读器。设备字体覆盖、所选字体缺字或 NFD 组合标记处理都可能造成观察到的字形差异。实际采用的字体和 Ocean 6 Pro 上的触发条件待实机确定。

## 方案与许可证

- 设备内置字体适合先做诊断。若某个可选拉丁字体在 A 中已正确处理 NFC、NFD 和字重，用户可选它。固件提供的字体及覆盖范围需在设备上核实，EPUB 无法依赖固定的设备字体名称。
- B 使用 Noto Serif 2.004，来自 Debian `fonts-noto-core 20201225-2`。Source Serif 4 也是可比较的静态字体方案，上游以 OFL 1.1 发布。B 只打包已经做过字形和排版检查的 Noto Serif。[Noto Debian 源包及许可](https://sources.debian.org/src/fonts-noto/20201225-2/debian/copyright/) · [Source Serif 许可](https://github.com/adobe-fonts/source-serif/blob/release/LICENSE.md)
- 四个 Noto Serif 静态 TTF 由 FontTools 子集化，保留拉丁扩展、组合重音和相关标点，以及字体的名称、许可和排版特性。四个原字体名称表均声明 SIL OFL 1.1，嵌入标志均为 0，适用许可未声明保留字体名称。OFL 允许在 EPUB 中嵌入、子集化和再分发。B 的 OPF `dc:rights` 包含版权声明与完整 OFL，字体内保留许可声明。[OFL 嵌入 FAQ](https://openfontlicense.org/ofl-faq/)
- 候选避免依赖可变字体轴。只有显式标记的法语区域优先使用内嵌字体，法语段中的显式中文、英文片段使用阅读器通用 serif 回退。内嵌字体本身没有汉字字形，未标记的混排汉字也会回退。独立中英文文章沿用基线字体选择。阅读器自己的目录面板可采用系统字体，导读和分隔页则是书内 XHTML，可作为对照。

## 启用与回滚

仅在本地合成测试或明确的验证环境设置 `EPUB_FRENCH_FONT_DIR`。目录需提供可再分发的静态 `regular.ttf`、`bold.ttf`、`italic.ttf`、`bold-italic.ttf` 和含版权声明及完整许可的 UTF-8 `OFL.txt`。读取失败会直接中止生成。清除此变量并重新生成即可移除内嵌字体和 CSS 声明。语言标记与原有 CSS 的链接修复独立生效，正文及分隔页可能开始显示原有样式中的间距和标题规则。

文章语言依次来自 `Article.metadata.language`、已有的 `Article.metadata.quality.language`、明确配置的来源 `metadata.epub_language`。只给已知单语来源配置来源级兜底。无可信声明时沿用界面语言，也不会据此启用候选字体。文章法语和英语分别输出 `fr` 与 `en`，其他支持的语言保留脚本和地区子标签。OPF 保留主界面语言，并追加已知文章语言。正文保留输入的 NFC、NFD、标点和空格，没有在生成阶段做 Unicode 转写。

从仓库根目录用生产生成器重建样书：

```sh
python -m scripts.epub_font_specimens /path/to/specimens /path/to/font-pack
epubcheck /path/to/specimens/A-baseline.epub
epubcheck /path/to/specimens/B-font-fix-candidate.epub
```

## Ocean 6 Pro 实机步骤

1. 记录固件版本、当前字体名、字号和粗细。分别导入 A、B，保留相同字号、行距、页边距和刷新模式。初次比较时若有“原书字体”“出版方样式”或类似选项，启用它并停用用户字体覆盖。菜单名称以固件实际显示为准。
2. 从书内导读进入分隔页和法语正文。分别比较普通拉丁字母与 `é è ê à ç œ` 的字形、字重和基线，检查 NFC/NFD 两行的重音应落在同一字母上，且无脱离、叠加或缺字。核对粗体、斜体、粗斜体、`« »`、弯撇号和不换行空格，再查看独立中文、英文及混排段。换行位置可因字宽而异，标点和词义应完整。记录书内导读与阅读器目录面板的表现。
3. 在 B 中选一种明显不同的用户字体（若有无衬线体可用，优先用它），观察法语正文 `a`、`g`、`œ` 及粗斜体是否变形。再切回原书样式并复查 A/B。若 B 在原书模式出现独有的衬线字形，而强制字体后与 A 趋同，说明阅读器的用户字体选项会覆盖书籍字体。若 B 始终跟随 A，可先重新导入 B 以排除缓存，再尝试另一套设备字体。两书仍一致时，设备可能忽略了嵌入字体或字体规则，也可能所选字体外形接近，视觉一致本身无法排除后者。
4. 若 A 和 B 只在强制选择某个设备字体时异常，优先调查该字体及其回退链。若只有 B 的法语变得协调，保存 A/B 同段照片与字体设置。若 B 的 NFC 正常而 NFD 异常，记录具体词与位置，进一步确认设备对组合重音的处理。中文或英文若有退化，同样记录对应段落和设置。
5. 验收时回报固件、设置、A/B 截图和各项结果。实机通过前正式出刊维持默认字体配置。

## 文件检查

- EPUBCheck 5.3.0 按 EPUB 3.3 规则检查两份 EPUB 3.0，均为 0 fatal、0 error、0 warning。
- EPUB 结构检查覆盖 OCF、OPF 语言、XHTML 的两种语言属性、样式表引用、字体路径及 manifest。两书对应 XHTML 逐字节相同。
- 四个字体均包含两行法语所需的字符、NFD 组合符、连字、引号、弯撇号与两种不换行空格。Pillow 的 RAQM 排版在四个字重下对两行样例的 NFC/NFD 输出逐像素相同。Ocean 6 Pro 的渲染结果待实测。
- 仓库测试套件（`pytest tests/`）共 741 项通过，4 项跳过。
