"""系统托盘模式（推荐的开源发行形态）。

把行情做成 Windows 通知区域（系统托盘）里的常驻图标，和飞书、微信等
图标同排显示，永远不会被其它窗口盖住。这是"任务栏小组件"在 Windows
上最稳定的实现方式，无需 SetParent 嵌入任务栏那种脆弱的做法。

交互设计（托盘形态下"滚动"的等价物）:

* 图标本身: 循环轮播每只股票的涨跌幅，红涨绿跌，一眼看方向。
* 悬停 tooltip: 显示当前轮播股票的完整信息（名称 现价 涨跌幅）。
* 右键菜单: 一次性列出所有股票的现价与涨幅，另有"立即刷新""退出"。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .quotes import Quote, fetch_quotes
from .widget import TickerWidget

logger = logging.getLogger(__name__)

# 托盘图标画布尺寸。Windows 会把它缩放到 16~32px，画大一点缩放后更清晰。
_ICON_SIZE = 64


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """尽量加载中文可用的字体，失败回退到 PIL 内置位图字体。"""
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class TrayTicker:
    """系统托盘行情图标。"""

    def __init__(self, cfg: Config, config_path: str | None = None) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self._quotes: list[Quote] = []
        self._lock = threading.Lock()
        self._rotate_idx = 0
        self._stop = threading.Event()
        # 设置窗口句柄，防止重复打开
        self._settings_win = None

        # 悬浮行情条控制器（惰性启动其独立 Tk 线程）+ 全局热键监听器
        self._floater = FloatController(cfg, config_path=self.config_path)
        self._hotkey_listener = None

        self._icon = pystray.Icon(
            "stock_glance",
            icon=self._render_icon(None),
            title="股票行情加载中…",
            menu=self._build_menu(),
        )

    # ---- 颜色 -------------------------------------------------------------

    def _color_for(self, quote: Quote | None) -> str:
        if quote is None:
            return self.cfg.flat_color
        if quote.is_up:
            return self.cfg.up_color
        if quote.is_down:
            return self.cfg.down_color
        return self.cfg.flat_color

    # ---- 图标渲染 ---------------------------------------------------------

    def _render_icon(self, quote: Quote | None) -> Image.Image:
        """把当前轮播股票的涨跌幅画进图标。

        上半部一个涨/跌箭头，下半部涨跌幅数字，整体用红/绿着色。
        """
        img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = self._color_for(quote)

        if quote is None:
            # 未取到数据时画一个中性的问号
            font = _load_font(40)
            draw.text((_ICON_SIZE / 2, _ICON_SIZE / 2), "…", font=font,
                      fill=color, anchor="mm")
            return img

        # 箭头: 上涨▲ / 下跌▼ / 平—
        arrow = "▲" if quote.is_up else ("▼" if quote.is_down else "—")
        arrow_font = _load_font(26)
        draw.text((_ICON_SIZE / 2, 16), arrow, font=arrow_font,
                  fill=color, anchor="mm")

        # 涨跌幅百分比，去掉小数点后多余位，保证小图标下也能塞下
        pct = f"{quote.change_pct:+.1f}"
        pct_font = _load_font(24)
        draw.text((_ICON_SIZE / 2, 44), pct, font=pct_font,
                  fill=color, anchor="mm")
        return img

    # ---- tooltip / 菜单文本 ----------------------------------------------

    def _line_for(self, q: Quote) -> str:
        return f"{q.name}  {q.price:g}  {q.change_pct:+.2f}%"

    def _tooltip_for(self, quote: Quote | None) -> str:
        """悬停 tooltip：一次性列出所有观察股票的名称+现价+涨跌幅。

        Windows tooltip 有长度上限（约 127 字符），超出时截断并提示。
        """
        with self._lock:
            quotes = list(self._quotes)
        if not quotes:
            return "股票行情（暂无数据，检查网络或股票代码）"
        lines: list[str] = []
        total = 0
        for q in quotes:
            line = self._line_for(q)
            # +1 为换行符，预留 "…" 提示空间
            if total + len(line) + 1 > 120:
                lines.append("…")
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    def _build_menu(self) -> pystray.Menu:
        """右键菜单：所有股票的现价+涨幅，加上设置/刷新/退出。"""
        items: list[pystray.MenuItem] = []
        with self._lock:
            quotes = list(self._quotes)
        if quotes:
            for q in quotes:
                # 菜单项文本用闭包锁定当前 quote，禁用点击（纯展示）
                items.append(pystray.MenuItem(self._line_for(q), None, enabled=False))
        else:
            items.append(pystray.MenuItem("暂无行情数据", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem(
            "显示悬浮行情条",
            self._on_toggle_float,
            checked=lambda item: self._floater.is_visible(),
        ))
        items.append(pystray.MenuItem("复原悬浮条位置", self._on_reset_position))
        items.append(pystray.MenuItem("设置…", self._on_settings))
        items.append(pystray.MenuItem("立即刷新", self._on_refresh_now))
        items.append(pystray.MenuItem("退出", self._on_quit))
        return pystray.Menu(*items)

    # ---- 菜单动作 ---------------------------------------------------------

    def _on_refresh_now(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _on_toggle_float(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """切换悬浮行情条的显示/隐藏（右键菜单入口，与热键等价）。"""
        self._floater.toggle()
        icon.update_menu()

    def _on_reset_position(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """把悬浮行情条复原到默认位置（右下角），避免被移出屏幕后找不到。"""
        self._floater.reset_position()

    def _register_hotkey(self) -> None:
        """按当前配置(重新)注册全局热键。空配置则不注册。"""
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._hotkey_listener = None
        spec = (self.cfg.hotkey or "").strip()
        if not spec:
            return
        try:
            from .hotkey import HotkeyListener

            listener = HotkeyListener(spec, self._floater.toggle)
            if listener.start():
                self._hotkey_listener = listener
                logger.info("已注册悬浮窗热键: %s", spec)
            else:
                logger.warning("热键注册失败（可能被占用）: %s", spec)
        except Exception as exc:  # noqa: BLE001 - 热键异常不应拖垮托盘
            logger.warning("热键注册异常: %s", exc)

    def _on_settings(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """打开设置窗口。

        tkinter 的 Tcl 解释器有强线程亲和性：若在临时线程里建窗口并跑
        mainloop，线程结束后进程主线程 GC 残留的 StringVar 会触发
        "main thread is not in main loop" / "Tcl_AsyncDelete" 崩溃。
        因此这里改用独立子进程调起设置窗（复用同一入口的 --settings），
        窗口跑在子进程真正的主线程里，关闭后子进程整体退出、干净回收。
        窗口保存成功（退出码 0）后，父进程读盘重载并热应用配置。
        已打开时不重复弹窗。
        """
        if self._settings_win is not None:
            return

        def _run() -> None:
            try:
                self._settings_win = True
                if getattr(sys, "frozen", False):
                    # PyInstaller 打包后：sys.executable 就是本 exe
                    cmd = [sys.executable, "--settings", "-c", self.config_path]
                else:
                    cmd = [
                        sys.executable,
                        "-m",
                        "stock_glance",
                        "--settings",
                        "-c",
                        self.config_path,
                    ]
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(cmd, creationflags=creationflags)
                ret = proc.wait()
                if ret == 0:
                    # 保存成功：读盘重载并热应用（回到托盘线程语境执行）
                    new_cfg = Config.load(self.config_path)
                    self._on_settings_saved(new_cfg)
            except Exception as exc:  # noqa: BLE001 - 设置窗口异常不应拖垮托盘
                logger.warning("设置窗口异常: %s", exc)
            finally:
                self._settings_win = None

        threading.Thread(target=_run, daemon=True).start()

    def _on_settings_saved(self, new_cfg: Config) -> None:
        """设置保存回调：热应用新配置并立即刷新一次。"""
        self.cfg = new_cfg
        logger.info("配置已更新，股票: %s", ", ".join(new_cfg.symbols))
        # 悬浮条按新配置重建（应用新样式/股票），热键按新配置重注册
        try:
            self._floater.update_config(new_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("悬浮窗热更新失败: %s", exc)
        self._register_hotkey()
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop.set()
        try:
            self._floater.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:  # noqa: BLE001
                pass
        icon.stop()

    # ---- 数据刷新 ---------------------------------------------------------

    def _refresh_once(self) -> None:
        try:
            quotes = fetch_quotes(
                self.cfg.symbols,
                source_type=self.cfg.source_type,
                source_url=self.cfg.source_url,
                api_key=self.cfg.api_key,
            )
        except Exception as exc:  # noqa: BLE001 - 网络异常不应中断托盘
            logger.warning("行情刷新失败: %s", exc)
            return
        if quotes:
            with self._lock:
                self._quotes = quotes
                if self._rotate_idx >= len(quotes):
                    self._rotate_idx = 0
            self._icon.menu = self._build_menu()
            self._icon.update_menu()
            self._update_display()

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_once()
            self._stop.wait(self.cfg.refresh_interval)

    # ---- 轮播显示 ---------------------------------------------------------

    def _current_quote(self) -> Quote | None:
        with self._lock:
            if not self._quotes:
                return None
            idx = self._rotate_idx % len(self._quotes)
            return self._quotes[idx]

    def _update_display(self) -> None:
        quote = self._current_quote()
        self._icon.icon = self._render_icon(quote)
        self._icon.title = self._tooltip_for(quote)

    def _rotate_loop(self) -> None:
        # 每 2 秒切换到下一只股票，模拟"滚动"轮播
        while not self._stop.is_set():
            self._update_display()
            self._stop.wait(2.0)
            with self._lock:
                if self._quotes:
                    self._rotate_idx = (self._rotate_idx + 1) % len(self._quotes)

    # ---- 启动 -------------------------------------------------------------

    def _on_setup(self, icon: pystray.Icon) -> None:
        icon.visible = True
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        threading.Thread(target=self._rotate_loop, daemon=True).start()
        # 注册全局热键；按配置决定是否开机即弹出悬浮条
        self._register_hotkey()
        if self.cfg.float_on_start:
            threading.Thread(target=self._floater.show, daemon=True).start()

    def start(self) -> None:
        logger.info("启动系统托盘行情图标，股票: %s", ", ".join(self.cfg.symbols))
        # 启动时对账开机自启：若已开启，刷新注册表命令，确保程序被移动/重装
        # 后自启路径仍然有效（幂等，命令不变则无副作用）。
        try:
            from . import autostart

            if self.cfg.auto_start:
                autostart.sync(True, self.config_path)
        except Exception as exc:  # noqa: BLE001 - 自启对账失败不应阻塞启动
            logger.warning("开机自启对账失败: %s", exc)
        self._icon.run(setup=self._on_setup)


class FloatController:
    """悬浮行情条的生命周期控制器（供托盘线程 / 热键线程安全调用）。

    TickerWidget 基于 tkinter，其对象必须在创建它的同一个线程里运行
    mainloop。因此本控制器把 widget 放进一条独立的守护线程内惰性启动，
    再通过 widget 暴露的线程安全接口（show/hide/toggle 内部只操作
    threading.Event）从任意线程控制显隐，关闭则用 request_stop 让
    Tk 线程自行销毁窗口，避免跨线程调用 root.destroy 引发崩溃。
    """

    def __init__(self, cfg: Config, config_path: str | None = None) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self._widget: TickerWidget | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _run(self) -> None:
        """在专属线程内创建并运行悬浮窗（阻塞于 mainloop 直到窗口关闭）。"""
        try:
            widget = TickerWidget(self.cfg, managed=True, config_path=self.config_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("悬浮窗创建失败: %s", exc)
            self._ready.set()
            return
        with self._lock:
            self._widget = widget
        self._ready.set()
        try:
            widget.start()  # 阻塞：运行 Tk mainloop
        except Exception as exc:  # noqa: BLE001
            logger.warning("悬浮窗运行异常: %s", exc)
        finally:
            with self._lock:
                self._widget = None
            self._ready.clear()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="float-widget"
            )
            self._thread.start()

    def show(self) -> None:
        """显示悬浮条（必要时惰性启动其线程）。"""
        self._ensure_started()
        self._ready.wait(timeout=5)
        with self._lock:
            widget = self._widget
        if widget is not None:
            widget.show()

    def hide(self) -> None:
        """隐藏悬浮条（不销毁窗口，线程继续存活以便快速再显示）。"""
        with self._lock:
            widget = self._widget
        if widget is not None:
            widget.hide()

    def toggle(self) -> None:
        """切换显示 / 隐藏；未启动时视为首次显示。"""
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            widget = self._widget
        if not alive or widget is None:
            self.show()
        else:
            widget.toggle()

    def is_visible(self) -> bool:
        with self._lock:
            widget = self._widget
        return bool(widget is not None and widget.is_visible())

    def update_config(self, new_cfg: Config) -> None:
        """应用新配置：重建悬浮窗以反映新样式 / 股票，并恢复原显隐状态。"""
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            widget = self._widget
            was_visible = bool(widget is not None and widget.is_visible())
        # 重建会触发 _position_window 重新定位，若不回填当前坐标，用户拖动后
        # 的位置会被 settings 表单里的默认值(-1)覆盖。趁旧 widget 尚存活，
        # 把它的实时坐标注入新配置，让重建后的窗口停在原地。
        if widget is not None:
            pos = widget.get_position()
            if pos is not None:
                new_cfg.pos_x, new_cfg.pos_y = pos
        self.cfg = new_cfg
        if not alive:
            return
        with self._lock:
            thread = self._thread
        self._request_stop()
        # ★根因修复（Tcl_AsyncDelete: async handler deleted by the wrong thread）：
        # Tkapp(Tcl 解释器)必须在**创建它的 Tk 线程**上析构。旧 widget 由主线程
        # 通过 self._widget 与本方法局部变量 widget 持有；只要主线程是最后一个
        # 释放引用者，Tkapp 的析构就发生在主线程 = 错误线程 → 崩溃。
        # 关键在于**释放时序**：必须在 join() 之前就丢弃所有主线程引用，使正在
        # 退出的 Tk 线程栈帧(其 self 指向该 widget)成为最后一个持有者。当该线程
        # target 返回、栈帧销毁时，widget 的引用计数归零，Tkapp 于 Tk 线程内被
        # 析构，join() 返回后主线程已无任何残留引用。
        with self._lock:
            if self._thread is thread:
                self._widget = None
                self._thread = None
        widget = None  # noqa: F841  丢弃局部引用，让 Tk 线程持最后一份
        if thread is not None:
            thread.join(timeout=3)
        if was_visible:
            self.show()

    def _request_stop(self) -> None:
        with self._lock:
            widget = self._widget
        if widget is not None:
            widget.request_stop()

    def reset_position(self) -> None:
        """把悬浮窗复原到默认位置。窗口未显示时先拉起再复原。"""
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            widget = self._widget
        if not alive or widget is None:
            self.show()
            with self._lock:
                widget = self._widget
        if widget is not None:
            widget.request_reset_position()

    def stop(self) -> None:
        """退出时关闭悬浮窗并等待其线程收尾。"""
        self._request_stop()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
