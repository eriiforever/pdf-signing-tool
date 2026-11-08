# PDF 盖章工具

把「整幅图幅大小的透明章图层 PDF」1:1 叠加到工程图纸或报告上，并配套一个开箱即用的图形界面程序。

本仓库**不含任何真实印章、真实图纸、单位名称或项目信息**。示例章是一个红框里写着「测试章」的示意图层，由 `tools/make_sample_seal.py` 生成。

## 三条铁律

1. **整幅图层叠加，绝不裁剪、缩放、旋转。** 章 PDF 是一整页透明图层，红色章只占其中一小块；保持整幅原尺寸叠加，章的相对位置自然保留。
2. **图层右下角对齐页面右下角**，逐页读取页面尺寸定位，不写死坐标。同一份图纸里常有 A2、加宽、加高等混合图幅。
3. **原件不动**：默认输出到「源文件目录\已签章\」；选择覆盖原件时先备份到 `_未盖章原件\`，已存在不覆盖。盖完逐页校验，返工先恢复。

## 版本

当前版本 **v2.1**。窗口标题、界面、启动日志、`--version` 与 exe 的 Windows 文件属性（FileVersion 2.1.0.0 / ProductVersion 2.1）都写入了版本号；升级时请同步修改 `src/stamp_gui.py` 的 `VERSION` 常量与 `tools/version_info.txt`。

| 版本 | 形态 | 说明 |
|---|---|---|
| v1.0 | 网页版 | 本地 HTTP 服务 + 内嵌 HTML 页面 + 浏览器；单文件 exe。已知问题：文件框层级、输出目录、关窗行为 |
| v2.0 | 独立窗口 | 首次脱离浏览器，tkinter 原生窗口 + pypdf，单文件 exe，不用装 Python |
| **v2.1** | **当前** | 同目录章自动发现、同名文件三策略、完成后打开输出文件夹、日志区弹性布局、内置脱敏示意章 |

详见 [CHANGELOG.md](CHANGELOG.md)、`releases/` 与 `history/`。

## 快速开始

- **直接用**：下载 v2.1 Release 附件 `PDF盖章工具-v2.1.exe`，双击即用；本地也交付了同名文件。
- **源码运行**：需 Python 3.9+ 且带 tkinter，`pip install -r src/requirements.txt`，然后 `python src/stamp_gui.py`。
- **拖拽**：把 PDF 拖到程序图标或 `src/stamp.bat` 上，自动带入文件列表或按默认设置盖章。
- **自行打包**：见 `tools/build_exe.md`。
- **生成示意章**：`python tools/make_sample_seal.py`（需 Pillow 与 pypdf）。

## 目录

```
src/         v2.1 图形界面源码
tools/       示意章、图标的生成脚本与打包说明
samples/     示意章生成结果（运行脚本后得到）
docs/        盖章规则、等效参数与踩坑记录
skill/       早期命令行版本（pdf-seal-stamp 技能，inspect/apply/verify/restore）
history/     各版本归档说明与未完成的实验源码
releases/    v1.0 / v2.0 / v2.1 的发布说明
```

## 依赖与许可

运行时仅需 `pypdf >= 6.0`（纯 Python）；窗口用 Python 标准库 tkinter。工具代码采用 MIT 许可，见 [LICENSE](LICENSE)。
