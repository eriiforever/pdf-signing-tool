---
name: pdf-seal-stamp
description: 给已有 PDF 加盖印章图层（出图章、公章、执业章、电子签章）时使用。把「整幅图幅大小的透明章 PDF」按 1:1 叠加到目标 PDF 的每一页或仅首页，支持右下角对齐、混合图幅、批量处理、原件备份与逐页校验。触发词：盖章、加章、盖个章、出图章、公章、签章、印章、章图片、stamp、seal、overlay。不适用于设计/生成章图片、PDF 文字提取与内容编辑。
version: 1.0.0
display_name: PDF 盖章（整幅图层叠加）
license: MIT
---

# PDF 盖章（整幅章图层叠加）

## 何时使用

用户给出一个**章 PDF**（通常是一整页透明图层，章只在其中某个位置）和一批**目标 PDF**，要求"盖章 / 加章 / 盖个出图章 / 首页盖公章"。典型场景是工程图纸（建筑图、结构图、可研报告、实施方案）批量签章。

不适用于：制作章图片、抠图去背景、PDF 文字/内容编辑、只读分析。

## 三条铁律（血泪教训，违反必返工）

1. **整幅图层叠加，绝不裁剪、绝不缩放、绝不旋转。**
   章 PDF 往往是 A2（1689.12×1195.92pt）或 A4（594.96×840.96pt）的整页透明图层，红色章只占其中一小块。把"章"裁出来再贴到页面角上是错的 —— 用户要的是整幅图按图幅定位，章在图层内的相对位置原样保留。
2. **默认右下角对齐**：整幅图层的右下角贴合每一页的右下角（`--anchor bottom-right`）。A2 页面即整页重合；加宽/加高页面随页面整体右移/下移，章自然落在右侧图签栏章位格。
   - 例外：用户明确说"原点对齐 / 按原图叠"时用 `--anchor top-left`；说"往左/往上挪一点"时用 `--dx` / `--dy` 微调（单位 pt，正=右/下）。
3. **逐页读取页面尺寸**，不要写死坐标。同一份图纸常混有 1689×1196、2112×1196、2531×1196、2390×1689 等多种图幅。

另两条操作纪律：
- **盖章前先备份原件**（脚本自动备份到 `<目标目录>/_未盖章原件/`，且备份已存在时不覆盖）。位置调整、返工一律从备份恢复后重盖，不要在已盖章文件上叠第二层。
- **盖完必须逐页校验**（`verify`），并渲染 1–2 页目视确认章落在图签栏章位格、无裁切、无越界。

## 决策表

| 用户说法 | 章源 | `--pages` | 锚点 |
|---|---|---|---|
| "这些图纸盖个出图章" | 出图章图层（A2） | `all`（默认，每一页） | `bottom-right`（默认） |
| "可研/实施方案首页盖个公章" | 公章图层（A4） | `first` | `bottom-right`（默认） |
| "和参考成品完全一样" | 按参考件的图幅与章位反推 | 通常 `all` | 通常 `bottom-right` |
| "章偏左 / 偏上 / 再靠右下" | 同上 | 同上 | 调 `--dx` / `--dy`，或从备份恢复后换锚点 |

判断"整幅图层"的方法：`inspect` 输出的章图层幅面若是标准图幅（A2≈1689×1196、A4≈595×841）而彩色章只占 100–200pt，就一定是整幅图层。

## 工作流

### 0. 环境

脚本只依赖 PyMuPDF。先探测可用的 Python（Windows 上 `python` 也可能是 `py -3`；若本机装过 pdfkit-py 技能，可直接用它的 venv）：

```bash
python -c "import pymupdf; print(pymupdf.version)" || python -m pip install -r "<SKILL_DIR>/scripts/requirements.txt"
```

下文统一用 `PY` 代表可用的解释器、`SKILL_DIR` 代表本技能目录。

### 1. 先分析，别急着写

```bash
PY "<SKILL_DIR>/scripts/stamp.py" inspect --seal "E:/.../示例章图层.pdf" --target "path/to/图纸.pdf"
```

输出：章图层幅面、章在图层内的 bbox 与距右/下边缘距离、目标每页尺寸、图层放置矩形、预计章位。核对无误再执行。

### 2. 盖章

```bash
# 单个/多个文件，逐页盖，原地覆盖 + 自动备份
PY "<SKILL_DIR>/scripts/stamp.py" apply --seal "E:/.../示例章图层.pdf" \
    --target "path/to/图纸.pdf" "E:/.../结构图.pdf"

# 整个目录批量，仅首页盖公章
PY "<SKILL_DIR>/scripts/stamp.py" apply --seal "E:/.../示例公章图层.pdf" \
    --dir "E:/.../1" --pages first

# 先演练（不写文件）
PY ".../stamp.py" apply --seal ... --dir ... --dry-run

# 另存新文件，不动原件
PY ".../stamp.py" apply --seal ... --dir ... --out-dir "E:/.../已盖章"
```

### 3. 校验

```bash
PY "<SKILL_DIR>/scripts/stamp.py" verify --dir "E:/.../新PDF - 副本"
```

逐页检测彩色章像素 bbox，打印各页尺寸、章位、距右边距离；缺章会列出页码并以退出码 2 失败。加 `--json` 时只输出纯 JSON（含 `ok` 与逐页坐标），便于程序化消费。同一批图纸里各页的"距右边缘"应基本一致（章随图层右移；低 dpi 采样会有 ±3pt 误差），这是最有效的正确性指标。随后渲染封面 / 内页 / 加宽页各一张目视确认。

### 4. 返工 / 微调

```bash
# 从备份恢复干净原件（备份已存在时不会被 apply 覆盖，永远安全）
PY "<SKILL_DIR>/scripts/stamp.py" restore --from-dir "E:/.../1/_未盖章原件" --to-dir "E:/.../1"

# 恢复后带微调重盖
PY ".../stamp.py" apply --seal ... --dir ... --dx -10 --dy -20
```

## 命令与参数速查

| 子命令 | 作用 |
|---|---|
| `inspect` | 分析章图层与目标页面，只打印 |
| `apply` | 盖章（默认原地覆盖 + 自动备份） |
| `verify` | 逐页校验章位置，缺章报错 |
| `restore` | 从 `_未盖章原件` 恢复原件 |

`apply` 主要参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--seal` | 必填 | 章图层 PDF（取第 1 页） |
| `--target` / `--dir` | 二选一 | 目标文件（可多个/通配符）/ 批量目录 |
| `--pattern` | `*.pdf` | `--dir` 的文件匹配模式（备份目录自动排除） |
| `--pages` | `all` | `all` / `first` / `1,3,5` / `2-4`（1 基页码） |
| `--anchor` | `bottom-right` | `bottom-right` / `top-left` / `top-right` / `bottom-left` / `center` |
| `--dx` `--dy` | `0` | 微调 pt，正=右/下 |
| `--out-dir` | — | 另存到目录，不改原件、不备份 |
| `--no-backup` / `--force-backup` | — | 关闭备份 / 覆盖已有备份（慎用） |
| `--check-existing` | — | 只检测「预计章位小区域」的彩色墨迹比例，达到阈值则跳过该页，避免重复盖章 |
| `--stamp-threshold` | `0.04` | 上述判定阈值（红色像素占比） |
| `--force` | — | 已有章也照常盖 |
| `--dry-run` | — | 只打印计划 |
| `--dpi` / `--color` | `60` / `auto` | 检测精度 / `auto`、`red`、`blue` |

`verify` 额外支持 `--json`（纯 JSON 输出）。`restore` 支持 `--from-dir`、`--to-dir`、`--pattern`、`--dry-run`。

## 排错与真实案例

详见 `references/cases.md`（含实际项目的章尺寸、混合图幅坐标、参考件比对方法、E 盘离线/章源丢失的应急处理、常见误判）。遇到"章位置不对"时**先读这份**，不要凭直觉改代码。

关键提示：
- 图纸本身可能有红色线条/文字，`verify` 的彩色检测可能把整页红字算进 bbox；此时用 `--color red` 配合局部渲染人工确认，不要盲目相信数字。
- 旋转页面（`page.rotation != 0`）会在 `inspect` 中标注；这类页面叠加坐标系可能不同，需目视复核。
- 加密 PDF 会直接报错，需先解密。
- 章源丢失时，若工作区有从同一章图层裁出的透明章 PDF，可用它与 `--anchor` / `--dx` / `--dy` 拼出等效坐标应急，但要向用户说明。

## 资源

- `scripts/stamp.py` —— 全部逻辑（inspect / apply / verify / restore），可直接执行
- `scripts/requirements.txt` —— 依赖
- `references/cases.md` —— 案例库与排错手册
- `README.md` —— 分发、安装与快速上手
