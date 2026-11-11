# v2.4 源码

- `stamp_gui.py`：主程序。双击/拖拽入口、tkinter 窗口、盖章核心、校验、同名策略都在这一文件。
- `stamp.bat`：Windows 启动器。双击开窗口，把 PDF 拖到 bat 上按默认设置直盖。
- `requirements.txt`：运行依赖（`pypdf`）。预览另需 `pypdfium2` 与 `Pillow`，缺了会自动降级。

运行：

```bash
pip install -r requirements.txt
python stamp_gui.py            # 打开窗口
python stamp_gui.py a.pdf b.pdf --pages first --out stamped
```

换章：把整幅透明章 PDF 放进程序旁的 `seals\`，或让文件名带「章」放在图纸同目录；窗口里点「浏览」也可以。

内置示意章由 `tools/make_sample_seal.py` 生成，打包时用 `--add-data` 嵌入 exe。

版本号：`stamp_gui.py` 的 `VERSION` 常量（当前 2.4）；`tools/version_info.txt` 是打包时写入 exe 文件属性的版本资源，两处需同步修改。
