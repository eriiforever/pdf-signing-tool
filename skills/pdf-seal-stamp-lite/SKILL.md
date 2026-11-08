---
name: pdf-seal-stamp-lite
description: 给已有 PDF 加盖印章图层（出图章、公章、执业章、电子签章）时使用。把用户提供的「整幅图幅大小的透明章 PDF」按 1:1 叠加到目标 PDF 的每页或仅首页，支持右下角对齐、混合图幅、批量处理、原件备份与逐页校验。触发词：盖章、加章、盖个章、出图章、公章、签章、印章、章图层、stamp、seal、overlay。不适用于生成或设计章图片、抠图去背景、PDF 文字与内容编辑。
version: 1.0.0
display_name: PDF 盖章 · 轻量版（零依赖）
license: MIT
---

# PDF 盖章 · 轻量版

把**整幅图幅大小的透明章图层 PDF** 1:1 叠加到目标 PDF 上。只用 `pypdf`（纯 Python），没有 GUI，也不需要 PyMuPDF、Qt 或浏览器。

## 重要：本技能不自带任何章

**技能包里没有、也不应该有章文件。** 每次执行都必须由使用者通过 `--seal` 提供自己的章图层 PDF；目标 PDF 同样由使用者提供。不要凭空生成章、不要把示例数据写进交付物。

## 何时使用

用户给出一枚**章 PDF**（通常是整页透明图层，章只占其中一小块）和一批**目标 PDF**，要求「盖章 / 加章 / 盖个出图章 / 首页盖公章 / 批量签章」。

不适用于：制作章图片、抠图、PDF 文字或内容编辑、纯读取分析。

## 三条铁律

1. **整幅图层叠加，绝不裁剪、缩放、旋转。** 章 PDF 往往是一整页透明图层，红色章只占其中一小块；把章裁出来再贴页面角会偏位，尤其在加宽图纸上。
2. **默认右下角对齐**：整幅图层的右下角贴合每页右下角。加宽、加高页面随页面整体右移 / 下移，章自然落在右侧图签栏。
3. **逐页读取页面尺寸**，不写死坐标；同一份图纸常混有多种图幅。

两条操作纪律：**盖章前先备份**（`--out overwrite` 会自动备份到 `_未盖章原件/`，已存在不覆盖）；**盖完必须 `verify`**，返回码 2 就恢复重盖，不要在已盖章文件上叠第二层。

## 决策表

| 用户说法 | `--pages` | `--out` |
|---|---|---|
| 「这些图纸盖个出图章」 | `all`（默认，每页） | `stamped`（默认，原件不动） |
| 「可研 / 实施方案首页盖个公章」 | `first` | `stamped` |
| 「只盖第 2 到 4 页」 | `2-4` 或 `1,3,5` | `stamped` |
| 「直接改原文件」 | 按需 | `overwrite`（自动备份） |
| 「输出到这个目录」 | 按需 | `custom --dir-out <目录>` |

## 环境

```bash
python -m pip install "pypdf>=6.0"
```

脚本路径：`scripts/stamp.py`。所有命令都支持 `--json`，退出码固定：**0 成功 / 1 出错 / 2 校验不通过**。

## 工作流

### 1. 先看清章和目标（只读）

```bash
python scripts/stamp.py inspect --seal path/to/seal.pdf --target path/to/drawing.pdf --json
```

返回章图层幅面、页数、目标每页图幅。若章图层幅面是标准图幅（A2 ≈ 1689×1196pt、A4 ≈ 595×841pt），基本可确认是整幅图层，按右下角对齐即可。

### 2. 盖章

```bash
python scripts/stamp.py apply --seal path/to/seal.pdf --target path/to/drawing.pdf --json
```

默认输出到「源文件目录/已签章/」，原件不动。批量可重复 `--target`、传文件夹或用通配符。

### 3. 逐页校验

```bash
python scripts/stamp.py verify --seal path/to/seal.pdf --file path/to/已签章/drawing.pdf --json
```

退出码 0 = 每页都带章；2 = `missing` 列出缺章页。

### 4. 返工

```bash
python scripts/stamp.py restore --target path/to/drawing.pdf --json
```

从 `_未盖章原件/` 复制回原文件，然后重新盖；不要在已盖章文件上再叠一层。

## 命令速查

| 命令 | 关键参数 |
|---|---|
| `inspect` | `--seal`、`--target`/`--dir`、`--json` |
| `apply` | `--seal`、`--target`/`--dir`、`--pages all\|first\|1,3-5\|2-4`、`--out stamped\|overwrite\|custom`、`--dir-out`、`--exists overwrite\|rename`、`--json` |
| `verify` | `--seal`、`--file`/`--target`/`--dir`、`--pages`、`--json` |
| `restore` | `--target`/`--dir`、`--json` |

## 输出与错误

- `--json` 输出 `{"ok": bool, ...}`；出错时 `{"ok": false, "error": "..."}` 并返回 1。
- `verify` 的 `missing` 是 1 基页码；返回 2 表示校验不通过，不要当成普通错误重试。
- 常见原因：章 PDF 加密、目标 PDF 加密、`--seal` 未提供或路径不存在、`--out custom` 缺 `--dir-out`。

更多参数换算与排错见 `references/params.md`。
