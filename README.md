# Folder Studio

一个 Windows 下的文件夹「外显名 / 图标 / 链接」小工具，用 Python + tkinter 写成，打包为单文件 exe 直接运行。

## 功能

- **外显名 / 悬浮提示 / 图标**：通过 `desktop.ini`（`A + H + S` 属性，与系统自建一致）设置文件夹显示名、鼠标悬停提示、自定义图标；文件属性可直接勾选只读 / 系统 / 隐藏。
- **创建链接**：符号链接、目录联接（Junction）、硬链接、快捷方式四种方式；附带方式对比说明与「一边改另一边」同步性提示；支持把文件 / 文件夹直接拖入窗口自动识别。
- **链接记录**：已创建的链接持久化记录（来源、方式、状态、时间），可排序、刷新状态、解除链接、移出列表；支持扫描磁盘收集现有链接。
- **链接角落图标（角标）**：为三种链接方式各自生成配色角标（符号链接蓝 / 目录联接绿 / 硬链接琥珀），可整体默认开启或按方式自定义 `.ico`；把 `.ico` 拖到预览或下方按钮即可快速设置。
- **设置**：界面文字缩放（60%–200%，±5% 微调）、`desktop.ini` 写入编码（默认 ANSI / GBK，兼容系统）、扫描跳过名单、扫描范围等。

## 运行

- 直接双击 `FolderStudio.exe`（位于 Releases）。
- 或源码运行（需 Python 3.12）：

  ```bash
  pip install pillow
  python folder_tool.py
  ```

  > 硬链接 / 目录联接在普通权限下可用；**符号链接通常需要以管理员身份运行**（程序内提供「以管理员重启」）。

## 构建

```bash
pip install pyinstaller pillow
pyinstaller --onefile --windowed --icon app.ico --name FolderStudio folder_tool.py
```

生成的 `dist/FolderStudio.exe` 即为单文件可执行程序；首次启动会在同目录生成 `ico/` 下的默认角标资源。

## 目录结构

```
folder_tool.py            主程序
app.ico                  窗口图标
FolderStudio.spec        PyInstaller 配置
ico/                     自带角标图标（运行时自动生成）
```

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
