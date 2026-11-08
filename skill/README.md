# PDF 盖章技能（可分发版）

给工程图纸、可研报告等 PDF 批量加盖印章图层。核心是**把整幅透明章 PDF 按 1:1 叠加**，不裁剪、不缩放，章在图层内的原始位置保持不变。

## 安装

1. 解压本压缩包，把整个 `pdf-seal-stamp` 文件夹放到技能目录：
   - 个人可用（所有项目）：`~/.workbuddy/skills/pdf-seal-stamp/`（Windows：`C:\Users\<用户名>\.workbuddy\skills\`）
   - 团队共享（单个项目）：`<项目>/.workbuddy/skills/pdf-seal-stamp/`
2. 安装依赖（脚本只用 PyMuPDF）：

   ```bash
   python -m pip install -r scripts/requirements.txt
   ```

   装过 `pdfkit-py` 技能的机器可直接使用它自带的 venv，无需重复安装。
3. 重启会话或在技能列表里刷新，之后说"给这些 PDF 盖个出图章"即可自动调用；也可以按下面命令手动运行。

## 三行上手

```bash
# 1) 先看章图层和目标页面（只打印，不写文件）
python scripts/stamp.py inspect --seal "章.pdf" --target "图纸.pdf"

# 2) 盖章：每一页，右下角对齐，原地覆盖并自动备份原件
python scripts/stamp.py apply --seal "章.pdf" --dir "图纸目录"

# 3) 逐页校验
python scripts/stamp.py verify --dir "图纸目录"
```

只盖首页（例如可研报告盖公章）：`apply ... --pages first`
另存而不改原件：`apply ... --out-dir "输出目录"`
位置微调：`apply ... --dx -10 --dy -20`（单位 pt，正=右/下）
返工：先 `restore --from-dir "图纸目录/_未盖章原件" --to-dir "图纸目录"`，再重新 `apply`。

## 目录内容

| 文件 | 说明 |
|---|---|
| `SKILL.md` | 技能说明、触发条件、三条铁律、完整工作流与参数表 |
| `scripts/stamp.py` | 主程序：`inspect` / `apply` / `verify` / `restore` |
| `scripts/requirements.txt` | 依赖 `PyMuPDF>=1.23` |
| `references/cases.md` | 真实案例参数、混合图幅坐标表、试错记录与排错手册 |

## 使用约定

- 章源应是**整页透明图层 PDF**（如 A2 出图章、A4 公章），脚本取其第 1 页。
- 默认锚点是图层右下角对齐页面右下角；混合图幅逐页取页面尺寸，不写死坐标。
- 默认原地覆盖目标文件，并自动把干净原件备份到同目录 `_未盖章原件/`（备份已存在时不覆盖）。
- 盖章后建议渲染 1–2 页目视确认，并运行 `verify`。

本技能不含任何印章图片或用户文档，可自由复制分发。
