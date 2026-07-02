"""命令行入口::

    python -m stock_glance              # 系统托盘模式（推荐，和飞书图标同排）
    python -m stock_glance --float      # 悬浮窗模式（贴屏幕右下角）
    python -m stock_glance -c my.json   # 指定配置文件
    python -m stock_glance -s 600519 000001 00700.HK   # 临时指定股票
"""

from __future__ import annotations

import argparse
import logging

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(description="任务栏股票行情组件")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("-s", "--symbols", nargs="+", help="临时指定股票代码，覆盖配置")
    parser.add_argument(
        "--float",
        dest="floating",
        action="store_true",
        help="使用贴屏幕右下角的悬浮窗，而非系统托盘图标",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument(
        "--settings",
        action="store_true",
        help="打开设置窗口后退出（供托盘在独立进程中调起，避免 tkinter 跨线程崩溃）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = Config.load(args.config)
    if args.symbols:
        cfg.symbols = args.symbols

    if args.settings:
        # 独立进程模式：托盘通过子进程调起本入口，让 tkinter 跑在真正的
        # 主线程里，彻底规避跨线程 Tcl 终结崩溃。窗口关闭后进程即退出。
        from .settings import open_settings

        saved = open_settings(cfg, args.config)
        raise SystemExit(0 if saved else 1)

    if args.floating:
        # 悬浮窗模式（旧行为，保留作为可选项）
        from .widget import TickerWidget

        cfg.embed_taskbar = False
        TickerWidget(cfg, config_path=args.config).start()
    else:
        # 默认：系统托盘图标，和飞书/微信等图标同排，永不被其它窗口盖住
        from .tray import TrayTicker

        TrayTicker(cfg, config_path=args.config).start()


if __name__ == "__main__":
    main()
