# 打包与分发说明

本文档说明如何从源码运行、打包成 Windows 单文件 exe，以及分发时的注意事项。

## 1. 目录结构

```
stock-glance/
├── README.md              项目说明与使用文档
├── PACKAGING.md           本文件：打包方法
├── LICENSE                MIT 许可证
├── pyproject.toml         包元数据 / pip 安装配置
├── requirements.txt       运行依赖
├── run.py                 打包入口（PyInstaller 用）
├── build.bat              一键打包脚本（Windows）
├── stock-glance.spec      PyInstaller 配置文件
├── config.example.json    配置模板（复制为 config.json 后编辑）
├── .gitignore
└── stock_glance/          源码包
    ├── __init__.py        导出 fetch_quotes 等公共 API
    ├── __main__.py        命令行入口（python -m stock_glance）
    ├── quotes.py          行情抓取与解析（腾讯主、新浪备）
    ├── tray.py            系统托盘模式（默认）
    ├── widget.py          悬浮窗模式（--float）
    ├── taskbar.py         win32 任务栏嵌入（可选）
    ├── settings.py        设置对话框
    ├── config.py          config.json 读写
    ├── autostart.py       开机自启动注册
    └── hotkey.py          全局快捷键
```

## 2. 从源码运行

需要 Python 3.9+。

```bash
pip install -r requirements.txt
python -m stock_glance
```

首次运行会在当前目录生成 `config.json`。命令行参数见 README。

## 3. 打包成单文件 exe

打包后用户无需安装 Python，双击 `stock-glance.exe` 即可运行。

### 方式一：一键脚本

```bash
pip install -r requirements.txt pyinstaller
build.bat
```

产物在 `dist\stock-glance.exe`（单文件，约 16 MB）。

### 方式二：使用 spec 文件

```bash
pip install pyinstaller
pyinstaller stock-glance.spec
```

### 方式三：手动命令

```bash
pyinstaller --onefile --windowed --name stock-glance ^
  --hidden-import pystray._win32 ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import win32gui --hidden-import win32con --hidden-import win32api ^
  --clean --noconfirm run.py
```

### 关键参数说明

| 参数 | 作用 |
| --- | --- |
| `--onefile` | 打成单个 exe |
| `--windowed` | 无控制台黑窗（GUI 应用） |
| `--hidden-import pystray._win32` | 托盘后端，PyInstaller 静态分析发现不了，漏了会在运行时报「找不到托盘后端」 |
| `--hidden-import PIL._tkinter_finder` | Pillow 绘制托盘图标依赖，漏了托盘图标无法生成 |
| `--hidden-import win32gui/con/api` | 悬浮窗定位与任务栏嵌入依赖 |
| `--clean --noconfirm` | 清理上次缓存并覆盖旧产物 |

> 注意：`build.bat`、`stock-glance.spec`、上面的手动命令三者的 hidden-import 保持一致。改依赖时三处要同步。

## 4. 分发注意事项

- exe 首次运行会在自身所在目录生成 `config.json`，用户可直接用记事本编辑（股票代码、颜色、刷新间隔等）。
- 分发时不要把开发机上的 `config.json` 一起打包，让用户拿到全新默认配置；仓库里保留 `config.example.json` 作为模板。
- exe 依赖 Visual C++ 运行库；绝大多数 Win10/11 已自带，极少数干净系统若报缺 dll，安装微软 VC++ Redistributable 即可。
- 行情来自第三方公开接口，需要能访问外网。

## 5. 作为 pip 包安装

```bash
pip install .
```

安装后可直接用命令 `stock-glance` 启动。
