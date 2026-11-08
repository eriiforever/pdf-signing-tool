# 参数换算与排错

## 放置公式

章图层尺寸 `sw × sh`（取章 PDF 第 1 页的 mediabox），目标页尺寸 `pw × ph`：

```
tx = pw - sw + page.left - seal.left
ty = ph - sh + page.bottom - seal.bottom
```

即「整幅图层右下角对齐页面右下角」，页面 mediabox 原点不为 0 时补上原点差。脚本已内置，无需手算。

## 混合图幅

同一份图纸常见多种页面尺寸（例如宽度 1689 / 2112 / 2531pt，高度 1196 / 1202 / 1695pt）。脚本逐页读取 `mediabox`，不需要额外参数。`inspect` 的 `page_sizes` 可以直接看到全部图幅。

## 页码

- `all`：每页（出图章）
- `first`：仅首页（公章）
- `1,3,5` / `2-4`：自定义，输入为 1 基页码；越界页会被忽略，全部越界则报错

## 同名输出

- `--exists overwrite`（默认）：直接覆盖 `已签章/` 下的同名文件
- `--exists rename`：生成 `xxx_已签章2.pdf`

覆盖模式（`--out overwrite`）不受此参数影响，而是先备份原件再替换。

## 输出体积

`apply` 会共用同一个章对象并对每页重新压缩内容流，输出通常只比原件大一点（约等于章图层大小）。如果明显膨胀，检查是否用了旧版本或被其它工具改写过。

## 排错

| 现象 | 处理 |
|---|---|
| `pypdf is required` | `python -m pip install "pypdf>=6.0"` |
| `seal file not found` / 未提供 `--seal` | 章文件必须由使用者提供，确认路径 |
| `seal PDF is encrypted` / `target PDF is encrypted` | 先用有权限的工具解密，再盖章 |
| `verify` 返回 2 | 看 `missing` 页码；从 `_未盖章原件/` 恢复后重盖，不要叠加 |
| `no valid page in range` | 页码范围超出文档页数 |
| `--out custom requires --dir-out` | 自定义输出必须给目录 |
| 章位置偏了 | 确认章 PDF 是**整幅透明图层**而不是裁好的小图；裁好的图只能按等效坐标使用，容易在加宽页偏位 |

## 视觉确认（可选）

本工具只做结构校验（页数 + 章图层对象是否进入每页内容流），不渲染图片。需要目视确认时，自行用任意 PDF 渲染工具出图检查章是否落在图签栏、有无裁切。
