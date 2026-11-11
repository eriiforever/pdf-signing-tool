# PDF 盖章工具

给工程图纸批量盖章的 Windows 小工具。把「整幅图幅大小的透明章图层 PDF」1:1 叠加到图纸上，右下角对齐、逐页适配图幅，不裁剪不缩放。另带一套给 AI 调用的零依赖命令行技能。

`v2.4` · Windows · MIT · 仓库内不含任何真实印章、图纸、单位或项目信息

[**下载 v2.4 程序**](https://github.com/eriiforever/pdf-signing-tool/releases/download/v2.4/PDFStamp-v2.4.exe) · [下载 AI 技能包](https://github.com/eriiforever/pdf-signing-tool/releases/latest/download/pdf-seal-stamp-lite-skill.zip) · [所有版本](https://github.com/eriiforever/pdf-signing-tool/releases)

## 能做什么

- **整幅图层 1:1 叠加**：不裁剪、不缩放、不旋转，章在图层内的相对位置原样保留
- **混合图幅自适配**：A2 及以上落位与旧版完全一致；A3、A4、加长图按章的墨迹贴回右下角，不再漏章；带 `/Rotate` 的页也正确处理
- **提取本单位电子章**：从一份盖过章的旧图纸里把章提出来，图像流与透明遮罩原样搬出，与原文件字节一致
- 每页 / 仅首页 / 指定页码；输出到「已签章」或覆盖原件（自动备份，已存在不覆盖）
- 同目录章图层自动发现、同名文件三策略、盖章后逐页校验、返工可从备份恢复
- 图形界面支持把 PDF 或整个文件夹直接拖进窗口添加
- 盖章前可预览首页叠章效果；可选择跳过已经盖过这枚章的页（默认关闭），勾选后重跑不会叠第二层
- 章位 dx/dy 微调并记住上次设置；换章自动套用该章记下的页码策略
- 可勾选生成 `盖章报告_时间.txt`（默认关闭），交付留痕

## 下载

| 文件 | 说明 |
|---|---|
| [`PDFStamp-v2.4.exe`](https://github.com/eriiforever/pdf-signing-tool/releases/download/v2.4/PDFStamp-v2.4.exe)（22 MB） | 单文件，双击运行，目标机不需要 Python |
| [`pdf-seal-stamp-lite-skill.zip`](https://github.com/eriiforever/pdf-signing-tool/releases/latest/download/pdf-seal-stamp-lite-skill.zip)（9.7 KB） | 给 AI 调用的技能包，不含任何章文件 |
| [`checksums.txt`](https://github.com/eriiforever/pdf-signing-tool/releases/download/v2.4/checksums.txt) | SHA-256 校验和 |

下载后双击即用，把 PDF 或文件夹拖进窗口就能添加文件。

## 快速开始

1. **放章**：把整幅透明章图层 PDF 放进程序旁的 `seals\` 文件夹（没有的话程序会自动建），或放在图纸同目录、文件名带「章」字，程序会自动扫到。
2. **加图**：把 PDF 或整个文件夹拖进窗口。
3. **选页码**：每页都盖（出图章）/ 仅首页（公章）/ 自定义（如 `1,3,5` 或 `2-4`）。
4. **点开始盖章**：默认输出到「源文件目录\已签章\」，原件不动。

没有章文件？点章图层那栏的「从已签章图纸提取章…」，选一份盖过电子章的旧图纸，程序会把它提取出来存进 `seals\`。

**从源码运行**：

```bash
python -m pip install -r src/requirements.txt
python src/stamp_gui.py
```

需要 Python 3.9+ 且自带 tkinter。预览功能需要额外装 `pypdfium2` 与 `Pillow`，没装时其它功能照常，只是点「预览」会提示。

**自行打包**：见 [`tools/build_exe.md`](tools/build_exe.md)。

**生成示意章**：`python tools/make_sample_seal.py`（需 Pillow 与 pypdf），产物不含任何真实印章信息。

## 版本

| 版本 | 说明 |
|---|---|
| [v1.0](https://github.com/eriiforever/pdf-signing-tool/releases/tag/v1.0) | 网页版：本地 HTTP 服务 + 内嵌 HTML + 浏览器。已知问题：文件框层级、输出目录、关窗行为 |
| [v2.0](https://github.com/eriiforever/pdf-signing-tool/releases/tag/v2.0) | 首次脱离浏览器：tkinter 原生窗口 + pypdf，单文件 exe，目标机无需 Python |
| [v2.1](https://github.com/eriiforever/pdf-signing-tool/releases/tag/v2.1) | 同目录章自动发现、同名文件三策略、完成后打开输出文件夹、日志区弹性布局 |
| [v2.2](https://github.com/eriiforever/pdf-signing-tool/releases/tag/v2.2) | 窗口内直接拖入 PDF / 文件夹；零依赖 AI 技能 |
| [v2.3](https://github.com/eriiforever/pdf-signing-tool/releases/tag/v2.3) | 盖章前预览、可选跳过已盖章页、章位微调、配置记忆、可选 txt 报告、CI 回归测试 |
| **[v2.4](https://github.com/eriiforever/pdf-signing-tool/releases/tag/v2.4)** | **当前版本** · 电子章提取（字节一致）、小图幅漏章修复、旋转页处理 |

各版本的变更明细见 [CHANGELOG.md](CHANGELOG.md)，发布说明在 `releases/`，已停止维护的实现在 `history/`。

## AI 技能

`skills/pdf-seal-stamp-lite/` 是给 Agent 调用的零依赖 CLI 技能，**不含任何章文件**，章由使用者通过 `--seal` 传入。

| 安装包 | 版本 | 更新 | 适配 |
|---|---|---|---|
| [`pdf-seal-stamp-lite-skill.zip`](https://github.com/eriiforever/pdf-signing-tool/releases/latest/download/pdf-seal-stamp-lite-skill.zip)（9.7 KB） | 1.0.0 | 2026-11-08 | Windows / macOS / Linux，Python 3.9+ |

### 让 AI 助手帮你装（推荐）

把下面这段整段复制给你的 AI 助手（Claude Code、Cursor、WorkBuddy 都行），它会自己下载、解压、校验并装到正确的目录，装完还会告诉你装在哪：

```
帮我安装这个 Agent Skill：
https://github.com/eriiforever/pdf-signing-tool/releases/latest/download/pdf-seal-stamp-lite-skill.zip

要求：
1. 下载后解压，确认目录里有 SKILL.md、scripts/stamp.py、references/params.md；
2. 装到我的 skills 目录（Claude Code 用 ~/.claude/skills，WorkBuddy 用 ~/.workbuddy/skills），
   最终路径应为 <skills>/pdf-seal-stamp-lite/；
3. 顺手执行 python -m pip install "pypdf>=6.0"；
4. 装完告诉我完整的安装路径。
```

### 手动安装

自己装到 Claude Code / Claude 的 skills 目录（`~/.claude/skills`），无需 clone 仓库：

**macOS / Linux**

```bash
mkdir -p ~/.claude/skills && \
curl -L https://github.com/eriiforever/pdf-signing-tool/releases/latest/download/pdf-seal-stamp-lite-skill.zip -o /tmp/skill.zip && \
unzip -oq /tmp/skill.zip -d ~/.claude/skills && rm /tmp/skill.zip
```

**Windows（PowerShell）**

```powershell
$dst = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Invoke-WebRequest "https://github.com/eriiforever/pdf-signing-tool/releases/latest/download/pdf-seal-stamp-lite-skill.zip" -OutFile "$env:TEMP\skill.zip"
Expand-Archive "$env:TEMP\skill.zip" -DestinationPath $dst -Force
```

WorkBuddy 用户把目标目录换成 `~/.workbuddy/skills`（Windows：`$env:USERPROFILE\.workbuddy\skills`）。装完重载技能列表或重启会话，Agent 就能发现它。

链接走 GitHub 的 `releases/latest`，始终指向最新正式版；如需锁定版本，把 URL 里的 `latest` 换成 `v2.4`。zip 解压后就是 `pdf-seal-stamp-lite/` 目录，直接落在 skills 目录下。

已经 clone 了仓库的话，也可以直接从源码目录复制：

```bash
mkdir -p ~/.claude/skills && cp -r skills/pdf-seal-stamp-lite ~/.claude/skills/
```

```powershell
Copy-Item -Recurse skills\pdf-seal-stamp-lite "$env:USERPROFILE\.claude\skills\"
```

### 用法

```bash
python -m pip install "pypdf>=6.0"
python skills/pdf-seal-stamp-lite/scripts/stamp.py inspect --seal path/to/seal.pdf --target path/to/drawing.pdf --json
python skills/pdf-seal-stamp-lite/scripts/stamp.py apply  --seal path/to/seal.pdf --target path/to/drawing.pdf --json
python skills/pdf-seal-stamp-lite/scripts/stamp.py verify --seal path/to/seal.pdf --file path/to/已签章/drawing.pdf --json
```

装在 skills 目录时，把路径换成 `~/.claude/skills/pdf-seal-stamp-lite/scripts/stamp.py`。四个子命令都支持 `--json`，退出码 0 / 1 / 2 分别表示成功、出错、校验不通过。

## 三条铁律

1. **整幅图层叠加，绝不裁剪、缩放、旋转。** 章 PDF 是一整页透明图层，红色章只占其中一小块；把章裁出来贴页面角会偏位，尤其在加宽图纸上。
2. **章要贴在页面右下角，但要按章的墨迹算，不是按图层边缘。** 图层往往比图幅大，图幅较小时（A3/A4/加长）按图层边缘对齐会把章推到纸外。
3. **原件不动，盖完校验。** 默认输出到「源文件目录\已签章\」；覆盖模式先备份到 `_未盖章原件\`（已存在不覆盖）。校验不过就从备份恢复重盖，不要叠第二层。

## 目录

```
src/       v2.4 图形界面源码（单文件 stamp_gui.py + 启动器）
skills/    AI 技能 pdf-seal-stamp-lite（零依赖 CLI）
tools/     示意章 / 图标生成脚本、打包说明、版本资源
tests/     回归测试（10 项，pypdf 环境即可跑）
docs/      盖章规则、参数换算与踩坑记录
dist/      v1.0–v2.4 可执行文件（同时挂在各自 Release 上）
history/   历史实现归档：网页版说明、v2.0 说明、Qt 实验源码、旧命令行技能
releases/  v1.0 / v2.0 / v2.1 / v2.2 / v2.3 / v2.4 的发布说明
```

## 依赖与许可

运行时仅需 `pypdf >= 6.0`（纯 Python），窗口用标准库 tkinter。预览功能另需 `pypdfium2` 与 `Pillow`，缺失时自动降级、不影响盖章。代码采用 MIT 许可，见 [LICENSE](LICENSE)。
