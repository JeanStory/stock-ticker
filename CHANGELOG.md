# Changelog

本项目所有重要变更都记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.1] - 2026-07-03

### 修复
- 港股行情改用腾讯实时数据源，修复原先港股显示延迟约 15 分钟的问题。用户输入与配置格式无需改动，程序自动切换。

## [1.0.0] - 2026-07-02

首个公开发布版本。

### 新增
- 常驻系统托盘的实时股票行情小组件，托盘图标轮播各只股票的涨跌方向（▲▼）与涨跌幅，红涨绿跌。
- 鼠标悬停显示当前股票完整行情；右键菜单列出全部股票，支持「立即刷新」与「退出」。
- 双数据源自动容错：腾讯财经（主）→ 新浪财经（备），无需 API Key。
- 支持 A 股、港股、美股；港股代码自动补零到 5 位。
- 可选悬浮窗模式（`--float`）：贴屏幕边缘的行情条，支持 `vertical`（逐只停留）与 `scroll`（横向滚动）两种展示。
- 悬浮窗支持圆角、透明度、置顶、全局快捷键切换显隐（仅 Windows）。
- 悬浮窗模式可选嵌入任务栏（`embed_taskbar`），失败自动降级为普通悬浮窗。
- 开机自启动开关（写入当前用户注册表，仅 Windows）。
- 首次运行自动生成 `config.json`，支持自定义股票、颜色、字体、尺寸、刷新间隔等。
- 提供 `-s` 临时指定股票、`-c` 指定配置文件、`-v` 调试日志等命令行参数。
- 支持通过 PyInstaller 打包为 Windows 单文件 exe，双击即用，无需安装 Python。

[1.0.1]: https://github.com/JeanStory/StockGlance/releases/tag/v1.0.1
[1.0.0]: https://github.com/JeanStory/stock-glance/releases/tag/v1.0.0
