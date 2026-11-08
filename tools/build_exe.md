# 打包单文件 exe

环境：Windows、Python 3.9+ 且带 tkinter、已装 `pypdf` 与 `pyinstaller`。

1. 先生成资源：

```bash
python tools/make_sample_seal.py     # 得到 samples/示意章-测试.pdf
python tools/make_icon.py            # 得到 stamp.ico
```

2. 打包（把 `samples/示意章-测试.pdf` 放到命令里指定的位置）：

```bash
python -m PyInstaller --onefile --windowed --noconfirm ^
  --name PDF盖章工具 ^
  --icon stamp.ico ^
  --version-file tools/version_info.txt ^
  --paths src/_vendor ^
  --collect-all pypdf ^
  --add-data "samples/示意章-测试.pdf;." ^
  --add-data "stamp.ico;." ^
  --distpath dist --workpath build --specpath build ^
  src/stamp_gui.py
```

要点：

- `--windowed` 去掉控制台黑框；`--collect-all pypdf` 保证纯 Python 依赖齐全（约 13 MB）。
- 程序内置章名必须与源码里的 `BUILTIN_SEAL` 一致。
- 若把 pypdf 装在环境里而不是随包 `_vendor/`，可去掉 `--paths`。
- 不需要 PySide6、PyMuPDF 或浏览器。

`--version-file` 把版本写进 exe 的 Windows 文件属性（右键→属性→详细信息）。源码 `VERSION` 常量与 `tools/version_info.txt` 需同步维护。
