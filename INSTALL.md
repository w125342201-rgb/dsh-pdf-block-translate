# 安装与验证

本文件是给 DSH 会话直接复制使用的操作 prompt，以及装不上时的排查。bundle 目录：

```text
<本仓库的绝对路径>
```

安装、启用和卸载都走 `plugin_manager`，不要用 shell 手工 `pnpm/npm install`，也不要手改 profile 文件。

## 1. 三段可直接复制的 prompt

### (a) 安装并验证 preset-pdf-translate 为 active

把下面整段粘进一个**有 `plugin_manager` 工具**的会话：

```text
请安装这个 DSH bundle，并验证它的 preset 已激活：

- 使用 plugin_manager 的 install_bundle，target 用绝对路径：
  <本仓库的绝对路径>
- 不要用 shell 手工 pnpm/npm 安装，也不要手改 profile 文件。
- 安装后分别调用 list_bundles 和 list_plugins，报告：
  1) dsh-pdf-block-translate 是否已安装、是否启用；
  2) preset-pdf-translate 这一行的 activation 状态是否为 active；
  3) 如果出现 restart-required / failed / overridden，原样报告状态和诊断，不要声称安装成功。
- 最后给出结论：preset-pdf-translate 是否 active（是/否）。
```

### (b) 新会话验证工具列表

安装成功后**新开一个会话**，在模式选择里选“PDF 保版面翻译”，然后粘：

```text
请做一次“PDF 保版面翻译”模式的工具自检，不要调用任何 PDF 工具：

1) 确认当前会话模式是否为“PDF 保版面翻译”；
2) 列出你当前可用的全部工具，并逐项确认是否包含：
   pdf_prepare、pdf_view、pdf_submit、pdf_finish、pdf_profile_propose、pdf_evolve；
3) 确认工具列表里只有只读文件工具与 ask_user_question，没有 shell/终端、写文件、编辑文件、读图、subagent 之类工具；
4) 任何一项不符合，明确说“不符合”并指出缺了或多了什么；无法确认的项说“无法确认”，不要猜。
```

### (c) 翻译一篇 PDF 的示例第一句

把路径换成本机真实存在的 PDF，然后在“PDF 保版面翻译”模式里粘：

```text
请用当前“PDF 保版面翻译”模式翻译这篇 PDF：

<PDF 的绝对路径，例如 D:\papers\example.pdf>

要求：
- 中文保版面，页数/图片/分栏不变；
- 先 pdf_prepare，再分批 pdf_view / pdf_submit，最后 pdf_finish；
- 公式混排段落默认保留原文；参考文献、作者与单位、页码不译；
- 校验失败时按 errors 修，不要绕过；不要用 shell 或写文件工具；
- 完成后只报告输出路径、翻译块数、保留块数和 QC 结果，不要贴整篇译文。
```

输出默认是源 PDF 同目录下的 `<源文件名>_中文全文翻译.pdf`；如果在工作区归档，建议按 `<编号>_<中文标题>_中文全文翻译.pdf` 命名。

## 2. 排查

### 2.1 模式选择器里没有“PDF 保版面翻译”

按顺序检查：

1. **是不是旧会话**：preset 在会话创建时确定；安装前已存在的会话不会出现新模式。**必须新开会话**。
2. **bundle 是否装上**：`plugin_manager list_bundles` 应看到 `dsh-pdf-block-translate`，且状态为启用。
3. **preset 行是否 active**：`plugin_manager list_plugins` 找 `preset-pdf-translate`。若为 `restart-required`，重启 DSH 后再新开会话；若为 `failed`，看 2.2；若为 `overridden`，说明有更高优先级的 layer 覆盖了同名行，检查当前 profile 的 patch 是否也声明了 `preset-pdf-translate`。
4. **是否装到了当前 profile**：在另一个 profile/工作区安装不会影响当前会话。
5. **显示名**：会话模式名来自 preset 配置，固定为“PDF 保版面翻译”；Plugin Manager 的 bundle 标题才随 locale 变化（中文标题相同，英文为 “PDF layout-preserving translation”）。preset id 仍是 `pdf-translate`。

不要为了让它出现而手工改 `cordis.patch.yml` 或复制文件到 profile；修 bundle 后重装。

### 2.2 activation failed

`list_plugins` / Plugin Manager 会保留失败行和诊断。常见原因：

- `cordis.patch.yml` 语法错误，或行 id 重复；本次 bundle 的 Loader 行 id 应为 `preset-pdf-translate`。
- preset 的 `plugins` 里引用了不存在的包名或 package export；本 bundle 需要 `dsh-pdf-block-translate` 的 `.`、`./locale/*.json`、`./icon` 三个 export 都在。
- `package.json` 的 `files` / `exports` 与实际文件不一致；或 `icon.svg`、`locale/*.json`、`docs/DETAIL.md` 缺失、超出大小限制。
- 本地目录安装是**链接**方式（profile 里记的是 `link:` 路径）：安装后移动或删除 `<本仓库的绝对路径>` 会让下次启动解析失败；确实要搬家，搬完在新位置重新 `install_bundle` 一次。
- DSH 版本与 bundle 使用的 preset/patch 语法不兼容。

处理：保留完整诊断，修 bundle 文件，再用 `install_bundle` 重装；如果提示需要重启就重启。不要直接编辑已安装 profile 里的 patch 来“压掉”错误。

注意：Python/PyMuPDF 缺失通常**不会**让 activation failed，而是在调用 `pdf_prepare` / `pdf_finish` 时作为工具错误返回；不要混为一谈。

### 2.3 装了，但旧会话不生效

这是预期行为，不是故障：

- 已有会话保留创建时的 plugin 与 preset 版本；安装/重装不会注入旧会话。**新开会话**，再选“PDF 保版面翻译”。
- 如果替换了已安装的同名 package（而不是首次安装），需要重启 DSH 才能加载新的 JavaScript 模块；首次安装新 bundle 一般可由 HMR 生效，但新模式仍需新会话。
- 旧会话不仅没有新工具，也不应出现 `pdf_*` 工具；这是“普通会话零影响”的设计。

验证：旧会话里问“列出工具”应没有 `pdf_*`；新会话按 (b) 自检应有 6 个 `pdf_*`。

### 2.4 Python / PyMuPDF 缺失

**症状**：`pdf_prepare` 或 `pdf_finish` 返回 `ok=false`，errors 提到 `python` 找不到（ENOENT）、`ModuleNotFoundError: No module named 'fitz'`，或渲染阶段直接报 PyMuPDF 错误。

**检查**（在 DSH 外先确认同一个解释器）：

```text
python --version
python -c "import sys, fitz; print(sys.executable); print(fitz.__doc__)"
```

- 第一条能找到 Python，第二条能打印解释器路径和 PyMuPDF 信息，才算满足。
- 注意：`pymupdf` 是包名，`fitz` 是它提供的导入名；脚本用的是 `import fitz`。
- 不要假定 DSH 内置 runtime 有 PyMuPDF——它通常没有；本 bundle 用的是系统 Python。

**修复**：

```text
python -m pip install pymupdf
```

- 把 PyMuPDF 装到 `pdf-tools` 实际使用的那个解释器里；如果机器上有多个 Python，用下面的命令确认路径：

  ```text
  where python
  ```

- 如果 `python` 不在 PATH：把 preset 的 `pdf-tools` 配置项 `python` 改成 Python 可执行文件的绝对路径（例如 `C:\Users\<你>\AppData\Local\Programs\Python\Python314\python.exe`）。优先在 Plugin Manager 的插件配置里改；如果界面不暴露嵌套的 `pdf-tools` 行，就改 bundle 的 `cordis.patch.yml` 中 `pdf-tools.config.python` 后重新安装。改完新开会话（或按提示重启）再试。
- 如果安装后仍报缺模块：用 `python -c "import sys; print(sys.executable)"` 确认 `python` 指向的解释器与装包的解释器是同一个。

**其他相关依赖**：渲染使用 Windows 系统字体 `C:\Windows\Fonts\msyh.ttc` 与 `msyhbd.ttc`。这两个文件缺失时表现为渲染/QC 缺字失败，而不是 Python 报错；检查：

```text
Test-Path C:\Windows\Fonts\msyh.ttc
Test-Path C:\Windows\Fonts\msyhbd.ttc
```

## 3. 安装成功的判定标准

同时满足才算成功：

1. `list_bundles` 有 `dsh-pdf-block-translate` 且启用；
2. `list_plugins` 的 `preset-pdf-translate` 为 `active`；
3. 新会话模式选择里有“PDF 保版面翻译”；
4. 该模式工具列表为：只读文件工具 + `ask_user_question` + 6 个 `pdf_*` 工具；
5. 按 (c) 跑通一个小 PDF 的 `pdf_prepare` → `pdf_submit` → `pdf_finish`，返回 `ok: true`、`qc: PASS`。

