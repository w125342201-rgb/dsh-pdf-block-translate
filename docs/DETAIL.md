# PDF 保版面翻译 — 插件详情

> 包名：`dsh-pdf-block-translate`｜Loader 行：`preset-pdf-translate`｜agent preset id：`pdf-translate`｜显示名：**PDF 保版面翻译**

这是一个 DSH bundle：它的 patch 插入一个 agent preset，preset 里只挂载 persona、只读文件工具、`ask_user`，以及本 bundle 的 6 个 PDF 工具。它把“翻译 PDF”拆成确定性流水线——模型只负责逐块给中文译文，版面坐标由脚本负责，所以模型无法把排版搞坏。

## 1. 功能

- 把一篇带文本层的 PDF 原位译成简体中文：页数、图片位置、图表与正文分栏保持不变。
- 模型只填写 `translations_template.json` 里的逐块译文；抹除原文、按原 bbox 重排中文、缺字检查、成稿 QC 全部由 Python 脚本完成。
- 校验和 QC 不通过时工具返回 `ok=false`，不把结果当作成品交付；修复后重跑，不需要重新翻译所有块。
- preset 是**受限执行器**：没有 shell、没有写文件、没有编辑文件、没有读图工具，只能通过 6 个 PDF 工具产出 PDF。
- 普通会话零影响：工具与 persona 只出现在选择“PDF 保版面翻译”模式的会话里；安装 bundle 不会把 PDF 工具挂进其他模式。

适合：论文、补充材料、说明书等以 Word/LaTeX 导出、有文本层的 PDF。
不适合：纯扫描件/图片型 PDF（v0 没有 OCR），以及需要重排公式、重建图表、重画页面的任务。

## 2. 组成与架构

```
preset-pdf-translate (Loader 行, @deepseek-ai/dsh-agent-preset)
└─ agent preset: id=pdf-translate, name=PDF 保版面翻译, order=20
   ├─ persona              固定“prepare → 填模板 → finish”流程与翻译要求
   ├─ tool-fs              read=true, readImage=false, write=false, edit=false, applyPatch=false
   ├─ tool-ask-user        ask_user_question
   └─ pdf-tools            dsh-pdf-block-translate, config: python=python
        └─ index.js 注册 6 个工具
             ├─ pdf_prepare        → python/prepare.py + python/common.py
             ├─ pdf_view           → 直接读 translations_template.json
             ├─ pdf_submit         → 直接改 translations_template.json
             ├─ pdf_finish         → python/finish.py
             │                        ├─ vendor/pdf_translate.py（抹除 + 回填）
             │                        ├─ vendor/pdf_qc.py（七项检查）
             │                        └─ vendor/clean_orphan_lineart.py（清矢量残留）
             └─ pdf_profile_propose → python/profile_propose.py
```

| 层 | 路径 | 说明 |
| --- | --- | --- |
| 工具注册 | `index.js` | 注册 6 个工具；`pdf-tools` 的 `config.python` 决定调用哪个 Python（默认 `python`），`config.timeoutMs` 默认 600000 ms |
| 版面分析 | `python/common.py` | 分栏、分段、分类（body/caption/equation/reference/meta/authors/table_cell/formula_mixed） |
| 准备 | `python/prepare.py` | 生成模板与计划 |
| 定稿 | `python/finish.py` | 校验 → 渲染 → QC → 清残留 → 二次 QC |
| 建议 | `python/profile_propose.py` | 读 `run_record.json` 给 profile 调整建议，只建议不改代码 |
| 渲染/QC 内核 | `python/vendor/` | `pdf_translate.py`、`pdf_qc.py`、`clean_orphan_lineart.py` |
| profile 说明 | `profiles/*.json` | 检测特征与策略的**文档化占位**；v0 运行时不读取 |

## 3. 六个工具

### 3.1 `pdf_prepare(src, workdir?, profile?)`

分析源 PDF 并生成翻译模板。必须在任何翻译动作之前调用。

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `src` | 是 | 源 PDF 的绝对路径 |
| `workdir` | 否 | 工作目录；默认在源 PDF 旁生成 `<PDF 文件名>_zh_work` |
| `profile` | 否 | `auto`（默认）、`single-col-word`、`two-col-latex`；`auto` 与省略都会走自动判栏 |

在 workdir 生成：

- `blocks.json`：渲染器输入，含全部块；
- `translations_template.json`：**唯一需要编辑的翻译模板**，但只能通过 `pdf_view` / `pdf_submit` 读写；
- `plan.json`：不可变的计划、统计与 `blocks.json` 的 SHA-256；
- `plan.md`：给人看的计划摘要。

返回 JSON：`ok`、`workdir`、`profile`、`pages`、`translatable`、`kept`、块类型统计 `stats`、模板与计划路径。不修改源 PDF。

### 3.2 `pdf_view(workdir, offset?, limit?, only?, maxChars?)`

按批读取模板条目，用于翻译与进度检查。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `offset` | 0 | 起始下标 |
| `limit` | 25 | 每批条数，1–60 |
| `only` | `todo` | `todo` 未填译文的可译块、`done` 已填块、`all` 全部（含 keep=true） |
| `maxChars` | 12000 | 单批返回的 `en` 字符预算上限，2000–40000；超预算的条目 `en` 会被截断 |

条目字段：`id`、`page`（1 起）、`kind`、`keep`、`size`、`en`、`zh`、`label`。返回还带 `total`、`translatable`、`translated`、`remaining`、`count`。`keep=true` 的块也会在 `only=all` 中显示，便于检查哪些内容被保留。

### 3.3 `pdf_submit(workdir, entries[])`

把一批译文合并进 `translations_template.json`。条目结构：

```json
{ "id": "p1-3", "zh": "中文译文", "keep": false, "label": "图 1 | ", "bbox_override": [50, 100, 300, 140] }
```

- `id` 必须存在；出现未知 id 时整批返回 `ok=false`，且**这一批不会写盘**，修正 id 后重交。
- `keep` 可覆盖模板默认值；`zh` 是译文；`label` 是图注粗体前缀（≤16 字符）；`bbox_override` 是可选的 `[x0,y0,x1,y1]` 绘制框（必须在本页内）。
- `kind=formula_mixed` 默认 `keep=true`；只有模型能忠实还原其中的公式时，才把它设为 `keep=false` 并给出译文。
- 本工具**不渲染 PDF**，只改模板。

### 3.4 `pdf_finish(workdir, out?, allow_partial?)`

校验模板 → 渲染 → QC → 清孤儿线/矢量残留 → 二次 QC。这是唯一能产出 PDF 的工具。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `workdir` | 必填 | `pdf_prepare` 生成的工作目录 |
| `out` | `<src 去扩展名>_中文全文翻译.pdf` | 输出路径，默认在源 PDF 旁 |
| `allow_partial` | false | 见下方 v0 行为说明 |

成功时返回 `ok`、`out`、`profile`、`pages`、`translated`、`kept`、`cjk`、`seconds`、`qc: PASS`、`warnings`，并写出 `translations.json`、`step1.pdf`、`run_record.json`（需要清孤儿线时还会写 `step2.pdf`）。渲染结果先写到 `workdir/final_check.pdf`，二次 QC 通过后才原子替换为 `out`（`os.replace`）。

失败时返回 `ok=false` 与 `errors`（可能附 `warnings`），不生成也不覆盖输出 PDF；若 `out` 路径上原本有文件，会保持原样。workdir 内的 `step1.pdf`、`step2.pdf` 等是中间产物，不是交付物。

`allow_partial`（默认 false）：为 true 时，`keep=false` 但缺 `zh` 的条目降级为 warning 并继续渲染，因此成稿里会保留这些块的原文；其他校验与 QC 失败仍照常报错。默认 false 时缺译一律失败。它不是“跳过全部校验”，也不建议作为常规交付路径。

### 3.5 `pdf_profile_propose(workdir)`

可选，通常在 `pdf_finish` 成功后调用。读取 `run_record.json` 和 `translations_template.json`，报告块类型统计、保留/可译/已译数量，并给出建议（例如 formula_mixed 保留过多、仍有未填块、CJK 过少、QC 未通过）。建议是咨询性的：不自动修改 pipeline、不修改 `profiles/*.json`。

### 3.6 `pdf_evolve(workdir?, dry_run?)`

可选，通常在翻译成功后调用。读取 `cases/*/src.pdf` 与 `case.json`，回放所有已存 case，网格尝试 `clean_formula_ratio` 与 `clean_frag_max`；只有同时满足“每个 case 自动覆盖率下降不超过 5 个百分点、展示公式块数在 ±10% 内、总体提升 2–10 个百分点、至少 2 个 case”才把参数写入 `profiles/<id>.json` 的 `params`，旧版备份到 `profiles/_history/`。它只改数值参数，不改代码、不改契约、不生成正则；`dry_run=true` 只报告不写。

每次 `pdf_finish` 成功会自动把该次源 PDF 与统计写入 `cases/<sha1>/`，并向 `cases/lessons.md` 追加一行；`pdf_prepare` 会把最近 8 条 lessons 一并返回，模型开工前应先看。

## 4. 使用流程

1. **新会话选模式**：安装后在模式选择里选“PDF 保版面翻译”，旧会话不会出现该模式。
2. **第一句**：给出 PDF 绝对路径和翻译要求，例如：
   `用“PDF 保版面翻译”模式翻译这篇 PDF：D:\...\paper.pdf，输出中文保版面 PDF；公式混排段落默认保留原文。`
3. **prepare**：agent 调 `pdf_prepare`，读 `plan.md` 与统计，确认 profile 和保留块比例。
4. **view / submit 循环**：`pdf_view` 取一批 `todo` 条目 → 逐条给 `zh` → `pdf_submit` 合并；重复到 `remaining=0`。
5. **finish**：`pdf_finish` 定稿。失败时按 `errors` 定位到具体 id 修复，再重跑。
6. **profile_propose（可选）**：对保留比例或 QC 有疑问时调用，决定下一轮怎么调 profile。
7. **交付**：只报告输出路径、翻译块数、保留块数、QC 结果；不要把整篇译文贴回聊天。

## 5. Profile

v0 支持两种运行 profile，由 `pdf_prepare` 的 `profile` 参数选择；`auto` 与省略会自动判断。

| | `single-col-word` | `two-col-latex` |
| --- | --- | --- |
| 判栏 | 逐页判 single/two | 逐页判 single/two |
| 选择条件 | 双栏页数 < max(2, 总页数 ÷ 4) | 双栏页数 ≥ max(2, 总页数 ÷ 4) |
| `--colsplit` | 首页宽度 + 5pt | 首页宽度 × 0.5 + 8pt |
| 典型对象 | Word/MathType 导出的单栏论文、补充材料 | LaTeX/arXiv/PRL 类双栏论文 |
| 公式策略 | 展示公式保留；formula_mixed 默认保留 | 展示公式保留；formula_mixed 默认保留 |
| 图注 | `Figure`/`Fig.`/`Table`/`图`/`表` + 编号；caption 可译 | 同左；跨栏图注按段落处理 |

`profiles/single-col-word.json`、`profiles/two-col-latex.json`、`profiles/default.json` 是**文档化占位**：记录检测特征、colsplit 规则、参考文献检测、公式策略、图注 label 规则，供后续调参；当前运行时不读取这些 JSON。`default.json` 不是 `pdf_prepare` 的可选值，它记录两种 profile 共享的保守默认策略。

## 6. 校验与失败条件

模板校验（`pdf_finish` 内、渲染前）：

| 检查 | 失败条件 |
| --- | --- |
| 缺译 | `keep=false` 且 `zh` 为空；默认失败，`allow_partial=true` 时降级为 warning 并继续 |
| 过短 | `zh` 少于 2 个字符 |
| 字号 | 代表块字号 < 9pt，且 `kind` 不是 `caption`（疑似选到公式碎片） |
| 越页 | bbox（或 `bbox_override`）超出页面 |
| 覆盖公式 | 译文 bbox 与展示公式块交叠超过 bbox 面积的 15% |
| 私用区 | `zh` 含 PUA（U+E000–U+F8FF） |
| 替换符 | `zh` 含 U+FFFD |
| 缺字 | `zh` 含 msyh 缺字符号：`⁻ ẑ ∇ ǁ ⊗ ∘ ≪ ≫ ∝ ∼ ⋅` |
| 长度比 | `len(zh)/len(en)` 小于 0.08 或大于 0.90 |
| label | 超过 16 字符 |
| 空稿 | 没有任何可渲染条目，且未传 `allow_partial`；传了则放行渲染（极端情况等于基本未翻译的 PDF） |

成稿 QC（`pdf_qc.py` 七项）：页数一致；逐页实绘图像 bbox 一致；U+FFFD = 0；中文缺字 = 0；行首标点 = 0；中文压图（交叠 >20%）= 0；同栏相邻行纵向重叠 >1pt = 0 且越栏行 = 0。原文本来就重叠或跨栏的行会与原文对跑豁免，不算缺陷。

随后 `clean_orphan_lineart.py` 清掉落在已翻译块内的孤儿线，`finish.py` 再清一次完全落在擦除框内的矢量残留，最后做二次 QC。任一失败：工具返回 `ok=false`，内部退出码 2；不生成也不覆盖输出 PDF（目标路径已有文件保持原样），workdir 里可能留下 `step1.pdf`、`step2.pdf` 等中间件。

## 7. 已知限制（v0）

- **内联 MathType 公式段落默认保留原文**：`kind=formula_mixed` 默认 `keep=true`。模型只有在有把握忠实还原该公式时，才可 override 为 `keep=false` 并给出译文；否则保留原文。
- **展示公式默认保留原文**：`kind=equation` 不翻译，公式与编号留在原处。
- **参考文献、作者/单位、页码默认保留**：`reference`、`authors`、`meta`、`page_number` 均为 `keep=true`；表格单元格（`table_cell`）也默认保留。
- **没有 OCR**：纯扫描/图片型 PDF 没有可抽取的文本层，无法用本流水线翻译。
- **preset 内没有手工修补通道**：没有 shell、写文件、编辑文件、读图与 subagent；只能通过 6 个工具完成流程，不能手写 PDF 或绕过校验。
- **公式与特殊字符要改写**：译文中的数学符号必须改写为可渲染的 ASCII/希腊字母形式（例如 `CO₂` 写 `CO2`，`⁻¹` 写 `^-1`）；PUA、U+FFFD 与 msyh 缺字符号会导致校验失败。
- **长度与版面适配有限**：渲染器在原文 bbox 内自动缩字号来容纳译文；极长译文可能被压得很小，必要时用 `bbox_override` 借空白，但仍受页面与栏界约束。
- **字体依赖**：渲染使用 Windows 字体 `C:/Windows/Fonts/msyh.ttc` 与 `msyhbd.ttc`；字体缺失时渲染/QC 会失败。
- **profile 可调但未自动学习**：`profiles/*.json` 是文档占位；`pdf_profile_propose` 只给建议，不自动改参数。

## 8. 运行环境与配置

- **系统 Python**：工具调用 `python`（也可在 `pdf-tools` 的 `config.python` 中改成绝对路径），不是 DSH 内置 runtime。
- **PyMuPDF**：脚本通过 `import fitz` 使用 PyMuPDF；缺失时 `pdf_prepare` / `pdf_finish` 会报错。
- **字体**：`msyh.ttc` 与 `msyhbd.ttc`（Windows 微软雅黑）。
- **超时**：`pdf-tools` 的 `config.timeoutMs` 默认 600000 ms；大文档可调大。
- **输入**：PDF 需要有可抽取的文本层；`src` 使用绝对路径最稳妥。

## 9. 验证

1. 安装 bundle 后，Plugin Manager 的 bundle 详情应显示标题“PDF 保版面翻译”、说明文字、原创图标与本详情页；Loader 行 `preset-pdf-translate` 的 activation 为 active。
2. 新开会话，模式选择里应出现“PDF 保版面翻译”；旧会话不会出现。
3. 进入该模式后，会话工具应只有：只读文件工具、`ask_user_question`、`pdf_prepare`、`pdf_view`、`pdf_submit`、`pdf_finish`、`pdf_profile_propose`；不应有 shell、写文件、编辑文件、读图工具。
4. 用一篇 1–2 页的小 PDF 走完整流程：`pdf_prepare` → 少量 `pdf_submit` → `pdf_finish`；确认返回 `ok: true`、`qc: PASS`，输出 PDF 页数与源文件一致、图片位置不变、中文可读。
5. 同一个 PDF 重复 `pdf_finish` 应得到同样的校验结果；默认失败用例（例如故意留一个 `keep=false` 的空 `zh`）应返回 `ok=false`，且不生成/不覆盖输出 PDF。
6. 安装后开一个普通会话，确认工具列表里没有 `pdf_*` 工具——preset 外零影响。

## 10. 相关文件

- `README.md`：包说明、安装、使用与文件树。
- `INSTALL.md`：可直接复制的安装/验证/翻译 prompt 与排查。
- `locale/en.json`、`locale/zh.json`：Plugin Manager 显示用的标题、描述与详情页路径。
- `profiles/*.json`：profile 检测特征与策略的文档化占位。
- `python/`：prepare/finish/profile_propose 与渲染、QC 内核。

