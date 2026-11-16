# dsh-pdf-blockwise — PDF 保版面翻译

一个 DSH workspace bundle（MIT，v0.1.0）。安装后由 patch 插入一个 agent preset：

- Loader 行：`preset-pdf-translate`
- agent preset id：`pdf-translate`
- 显示名：**PDF 保版面翻译**

它和「调一个 PDF 翻译 API」的插件不是同一条路，有三个硬性区别：

1. **不用 API Key。** 翻译由当前会话的模型完成，装好就能用；没有额外账单，也不把 PDF 内容发给第三方服务。
2. **不需要多模态。** 模型只读文本块、只写中文，坐标与排版权全在 Python 里；纯文本模型即可，不依赖视觉能力。
3. **校验不过不出 PDF。** 抹除原文、按原 bbox 回填之后，`pdf_qc` 跑七项检查（缺译、bbox 覆盖公式、缺字、越界等），任一项不过就不落盘，返回 `ok=false` 让模型逐条改。

模型只负责逐块填写中文译文，版面坐标由 Python 脚本负责，因此模型无法把排版搞坏，也不会出现「只给摘要版」「逐页重排」这类降级。

普通会话零影响：persona、只读文件工具、`ask_user` 和 6 个 PDF 工具只挂在该 preset 的会话里，不会进入其他模式。

## 1. 架构

```text
preset-pdf-translate (Loader 行, @deepseek-ai/dsh-agent-preset)
└─ agent preset: id=pdf-translate, name=PDF 保版面翻译, order=20
   ├─ persona              固定“prepare → 填模板 → finish”流程
   ├─ tool-fs              read=true, readImage=false, write=false, edit=false, applyPatch=false
   ├─ tool-ask-user        ask_user_question
   └─ pdf-tools            dsh-pdf-blockwise（本 bundle），config: python=python
        └─ index.js 注册 6 个工具
             ├─ pdf_prepare        → python/prepare.py + python/common.py
             ├─ pdf_view           → 读 translations_template.json
             ├─ pdf_submit         → 改 translations_template.json
             ├─ pdf_finish         → python/finish.py
             │                        ├─ vendor/pdf_translate.py（抹除原文 + 按原 bbox 回填）
             │                        ├─ vendor/pdf_qc.py（七项检查）
             │                        └─ vendor/clean_orphan_lineart.py（清孤儿线）
             ├─ pdf_profile_propose → python/profile_propose.py
             └─ pdf_evolve         → python/evolve.py（回放 cases，写 profile params）
```

### 6 个工具

| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `pdf_prepare` | 分析 PDF，生成 `blocks.json` / `translations_template.json` / `plan.json` / `plan.md`，返回统计 | `src`（必填绝对路径）、`workdir`、`profile`（`auto`/`single-col-word`/`two-col-latex`） |
| `pdf_view` | 按批查看模板条目（`id/page/kind/keep/size/en/zh/label`），方便翻译 | `workdir`、`offset`、`limit`（1–60）、`only`（`todo`/`done`/`all`）、`maxChars` |
| `pdf_submit` | 把 `{id, zh?, keep?, label?, bbox_override?}` 合并进模板；不写 PDF | `workdir`、`entries` |
| `pdf_finish` | 校验 → 渲染 → QC → 清孤儿线/矢量残留 → 二次 QC；唯一产出 PDF 的工具 | `workdir`、`out`、`allow_partial` |
| `pdf_profile_propose` | 读 `run_record.json`，给出 profile 调整建议；只建议，不改代码/不改 profile | `workdir` |
| `pdf_evolve` | 回放 `cases/*`，网格试 `clean_formula_ratio` / `clean_frag_max`；满足覆盖率与公式稳定性约束才写 `profiles/<id>.json` 的 `params` | `workdir?`、`dry_run?` |

### Python 脚本

| 路径 | 作用 |
| --- | --- |
| `python/common.py` | 分栏、分段、块分类（body/caption/equation/reference/meta/authors/table_cell/formula_mixed），生成 plan |
| `python/prepare.py` | 生成 `translations_template.json`、`blocks.json`、`plan.json`、`plan.md` |
| `python/finish.py` | 校验模板 → 调渲染器 → 调 QC → 二次 QC → 写 `run_record.json` |
| `python/profile_propose.py` | 读运行记录给 profile 调整建议 |
| `python/vendor/pdf_translate.py` | PyMuPDF 坐标回填渲染内核 |
| `python/vendor/pdf_qc.py` | 七项成稿 QC（页数、图像、缺字、行首标点、压图、叠行、越栏） |
| `python/vendor/clean_orphan_lineart.py` | 清除落在已翻译块内的孤儿矢量线 |

`profiles/*.json` 是 profile 检测特征与策略的**文档化占位**：记录 columns、colsplit、reference 检测、公式策略、caption label 规则，方便后续调参；v0 运行时不读取它们。

## 2. 安装

### 前置条件

- 可用的 DSH，且当前会话有 `plugin_manager`。
- 系统 Python 与 PyMuPDF：脚本通过 `import fitz` 使用 PyMuPDF；不是 DSH 内置 runtime。
- Windows 字体 `C:/Windows/Fonts/msyh.ttc`、`msyhbd.ttc`（渲染器使用微软雅黑）。
- preset 的 `pdf-tools` 配置项 `python` 默认是 `python`；若 `python` 不在 PATH，改成 Python 可执行文件的绝对路径（改法见 `INSTALL.md` 的 2.4）。

### 步骤

1. 安装 bundle（不要在 shell 里手工 `pnpm install` 或改 profile）：

   ```text
   plugin_manager: install_bundle
   target: <本仓库的绝对路径>
   ```

2. 确认 bundle 已安装：`list_bundles` 中出现 `dsh-pdf-blockwise`。
3. 确认 preset 已激活：`list_plugins` 中 `preset-pdf-translate` 的 activation 为 `active`。若返回 `restart-required`，重启 DSH 后复查；若 `failed`，查看诊断并修 bundle 后重装。
4. **新开一个会话**，在模式选择里选 **“PDF 保版面翻译”**。

安装会执行 bundle 代码并修改当前 profile，需要 Full access 或用户批准。安装完成后旧会话不会自动获得这个模式。

## 3. 使用

1. 新开会话，选模式“PDF 保版面翻译”。
2. 第一句给出 PDF 绝对路径和要求，例如：

   ```text
   用“PDF 保版面翻译”模式翻译这篇 PDF：<PDF 的绝对路径，例如 D:\papers\example.pdf>

   要求：中文保版面，先 pdf_prepare，再分批 pdf_view / pdf_submit，最后 pdf_finish；
   公式混排段落默认保留原文，参考文献、作者/单位、页码不译；校验失败不要绕过。
   ```

   把路径换成要译的 PDF 即可。`pdf_prepare` 返回的统计里有总条目数和待译数，按它决定每次 `pdf_view` 取多少条（上限 60），别一次吃满。不要用 shell、写文件或手工改模板——该模式没有这些工具。
3. agent 会先 `pdf_prepare`，再循环 `pdf_view` → `pdf_submit` 填译文，最后 `pdf_finish`。若 `pdf_finish` 返回 `ok=false`，按 `errors` 定位到具体 id 修复后重跑；修复时不需要重新翻译其他块。
4. 成功后输出默认是源 PDF 同目录下的 `<源文件名>_中文全文翻译.pdf`。可用 `out` 参数改名或改目录；建议沿用工作区命名惯例 `<编号>_<中文标题>_中文全文翻译.pdf`。

### 运行时产物（workdir）

默认 workdir 是源 PDF 旁的 `<源文件名>_zh_work/`：

```text
<workdir>/
├─ blocks.json                 渲染器输入（不可手改）
├─ translations_template.json  翻译模板（只能经 pdf_view / pdf_submit 读写）
├─ plan.json                   计划、统计、blocks 哈希（不可改）
├─ plan.md                     给人看的计划摘要
├─ translations.json           定稿时由 finish 生成
├─ step1.pdf                   首次渲染中间件
├─ step2.pdf                   清孤儿线后的中间件（无需清理时不生成）
├─ final_check.pdf             二次 QC 临时文件（成功后改名为 out）
└─ run_record.json             成功运行记录（profile/块数/CJK/QC/耗时/warnings）
```

`pdf_finish` 失败时不会生成或覆盖输出 PDF；workdir 里可能留下 `step1.pdf`、`step2.pdf` 等中间件，不要当成品交付。

## 4. 验证

### 安装与模式

1. `list_bundles` 有 `dsh-pdf-blockwise`，`list_plugins` 的 `preset-pdf-translate` 为 `active`。
2. Plugin Manager 的 bundle 详情显示标题“PDF 保版面翻译”、说明文字、原创图标与 `docs/DETAIL.md` 详情页。
3. 新会话的模式选择器里出现“PDF 保版面翻译”；旧会话没有。
4. 进入该模式后确认工具集：只有只读文件工具、`ask_user_question` 和 6 个 `pdf_*` 工具；没有 shell、写文件、编辑文件、读图工具。

### 功能冒烟

5. 用一篇 1–2 页、有文本层的小 PDF 走完整流程：`pdf_prepare` → 少量 `pdf_submit` → `pdf_finish`。
6. 确认返回 `ok: true`、`qc: PASS`；输出 PDF 页数与源文件一致，图片位置不变，中文可读。
7. 检查 `plan.md` 的块类型统计与 `run_record.json` 的 `translated`、`kept`、`cjk`、`warnings`。
8. 失败用例：故意留一个 `keep=false` 且 `zh` 为空的条目，`pdf_finish` 应返回 `ok=false`、不生成/不覆盖输出 PDF（`allow_partial=true` 时降级为 warning 并继续，属于有意的例外）。

### 普通会话

9. 安装后开一个普通会话，确认没有 `pdf_*` 工具，工具列表与安装前一致——preset 外零影响。

## 5. 已知限制（v0）

- **内联 MathType 公式段落默认保留原文**：`kind=formula_mixed` 默认 `keep=true`。模型可在有把握时 override 为 `keep=false`，并给出忠实还原公式的译文；否则保留原文。
- **展示公式默认保留原文**：`kind=equation` 不译，公式与编号留在原处。
- **参考文献、作者/单位、页码默认保留**：`reference`、`authors`、`meta`、`page_number` 均为 `keep=true`；表格单元格（`table_cell`）也默认保留。
- **没有 OCR**：纯扫描/图片型 PDF 没有文本层，不能处理。
- **preset 内没有手工修补通道**：没有 shell、写文件、编辑文件、读图与 subagent；只能走 6 个工具，不能手写 PDF 或绕过校验。
- **公式与特殊字符要改写**：译文中的数学符号需写成可渲染的 ASCII/希腊字母（如 `CO₂` 写 `CO2`，`⁻¹` 写 `^-1`）；PUA、U+FFFD 与 msyh 缺字符号会导致校验失败。
- **版面适配有限**：渲染器在原文 bbox 内自动缩字号；极长译文可能被压得很小，必要时用 `bbox_override` 借用空白，但仍受页面与栏界限制。
- **profile 是保守规则**：`auto` 只在 `single-col-word` 与 `two-col-latex` 之间二选一；`profiles/*.json` 目前只是文档占位，调整需要改脚本，`pdf_profile_propose` 不会自动修改。
- **字体与 Python 依赖**：需要系统 Python + PyMuPDF，以及 Windows 字体 `msyh.ttc` / `msyhbd.ttc`；任一缺失都会让 prepare/finish 失败。

## 6. 文件树

```text
pdf-translate-bundle/
├─ .gitignore                   # 排除 __pycache__ / *.pyc
├─ package.json                 # dsh-pdf-blockwise, bundle patch 入口
├─ cordis.patch.yml             # 插入 preset-pdf-translate（agent preset pdf-translate）
├─ index.js                     # 注册 6 个 pdf_* 工具
├─ icon.svg                     # Plugin Manager 图标（64×64）
├─ README.md                    # 本文件
├─ INSTALL.md                   # 安装/验证/翻译 prompt 与排查
├─ locale/
│  ├─ en.json                   # meta.title/description/detail（英文，sentence case）
│  └─ zh.json                   # meta.title/description/detail（中文）
├─ docs/
│  └─ DETAIL.md                 # 插件详情页文档（功能/工具/流程/限制/验证）
├─ profiles/
│  ├─ default.json              # 通用保守默认策略（文档占位）
│  ├─ single-col-word.json      # 单栏 Word 型检测特征与策略
│  └─ two-col-latex.json        # 双栏 LaTeX 型检测特征与策略
└─ python/
   ├─ common.py                 # 版面分析、分栏、分段、分类
   ├─ prepare.py                # 生成翻译模板与计划
   ├─ finish.py                 # 校验 → 渲染 → QC → 二次 QC
   ├─ profile_propose.py        # 读 run_record 给 profile 建议
   └─ vendor/
      ├─ pdf_translate.py       # 坐标回填渲染内核
      ├─ pdf_qc.py              # 七项成稿 QC
      └─ clean_orphan_lineart.py# 清孤儿矢量线
```

`__pycache__/` 是 Python 生成的缓存，未列入。运行时会在源 PDF 旁生成 `<名称>_zh_work/`，也不属于 bundle 源码。

## 7. 相关文档

- 安装、验证与排查的具体 prompt：`INSTALL.md`
- 插件详情页：`docs/DETAIL.md`
- 运行前请确认输入 PDF 有文本层，且输出目录可写；本 bundle 不负责 PDF 扫描、OCR、公式重排或图表重建。

