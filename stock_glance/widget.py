"""滚动行情组件（tkinter 实现）。

用一个无边框、置顶的 Tk 窗口承载 Canvas，把所有股票拼成一条长文本，
在 Canvas 上从右向左匀速滚动。行情数据由后台线程按间隔刷新，主线程
只负责绘制，二者通过一个加锁的快照列表交换数据，避免 UI 卡顿。
"""

from __future__ import annotations

import gc
import logging
import threading
import tkinter as tk
import tkinter.font as tkfont

from .config import Config
from .quotes import Quote, fetch_quotes
from . import taskbar

logger = logging.getLogger(__name__)


def _enable_dpi_awareness() -> None:
    """让进程感知系统 DPI 缩放。

    非 DPI-aware 的进程在 >100% 缩放的显示器上会被 Windows 做位图拉伸，
    导致文字发糊、``winfo_screenwidth`` 返回虚拟(缩小)分辨率、窗口定位错乱。
    在创建 Tk() 之前调用一次即可。非 Windows 环境静默跳过。
    """
    try:
        import ctypes

        try:
            # Win 8.1+：PROCESS_PER_MONITOR_DPI_AWARE = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            # 退回 Vista+ 的进程级 DPI 感知
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


class TickerWidget:
    def __init__(
        self,
        config: Config,
        *,
        managed: bool = False,
        config_path: str | None = None,
    ) -> None:
        self.cfg = config
        # 配置文件路径：右键切换"置顶显示"时用它把改动写回磁盘，
        # 让独立进程的设置对话框下次打开能读到同步后的值。为 None 时
        # 回退到 Config.save 的默认路径。
        self._config_path = config_path
        self._quotes: list[Quote] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._offset = 0  # 当前滚动偏移
        self._text_ids: list[int] = []

        # managed 模式：由托盘控制器托管，右键/关窗只隐藏不退出，
        # 显示/隐藏由后台线程置位 _want_visible，Tk 线程轮询后执行。
        self._managed = managed
        self._want_visible = threading.Event()
        if config.float_on_start:
            self._want_visible.set()
        self._is_visible = True  # root 初始为显示态

        _enable_dpi_awareness()

        self.root = tk.Tk()
        self.root.title("StockGlance")
        self.root.overrideredirect(True)   # 去掉标题栏/边框
        self.root.attributes("-topmost", bool(self.cfg.always_on_top))
        # 解析背景样式预设:决定卡片背景色、主/次文字色、是否整窗透明。
        pal = self.cfg.palette()
        self._transparent_mode = pal["transparent"]
        self._card_bg = pal["bg"]
        self._fg = pal["fg"]
        self._muted = pal["muted"]
        # 圆角卡片：用透明色键把窗口四角"抠"成圆角。若系统不支持则降级为直角纯色。
        self._round = self.cfg.corner_radius > 0
        self._transparent_key = "#010203"  # 一个几乎不会与内容撞色的魔法色
        canvas_bg = self._card_bg
        if self._transparent_mode:
            # 完全透明样式：用窗口级 alpha 半透明透出桌面。
            # 不用色键抠透，因为色键透明像素在 Windows 上会被鼠标穿透，
            # 导致透明背景区无法拖拽；alpha 半透明则整窗可点可拖。
            try:
                self.root.attributes("-alpha", self.cfg.transparent_alpha)
                self.root.configure(bg=self._card_bg)
                canvas_bg = self._card_bg
                # alpha 模式下不再用色键抠圆角，避免透明像素穿透。
                self._round = False
            except Exception:  # noqa: BLE001
                # 系统不支持窗口 alpha 时降级为不透明深色卡片，保证文字可读。
                self._transparent_mode = False
                self.root.configure(bg=self._card_bg)
        elif self._round:
            try:
                self.root.attributes("-transparentcolor", self._transparent_key)
                self.root.configure(bg=self._transparent_key)
                canvas_bg = self._transparent_key
            except Exception:  # noqa: BLE001
                self._round = False
                self.root.configure(bg=self._card_bg)
        else:
            self.root.configure(bg=self._card_bg)

        self.canvas = tk.Canvas(
            self.root,
            width=self.cfg.width,
            height=self.cfg.height,
            bg=canvas_bg,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        # 三套字体：常规（名称/涨跌幅）、加粗（现价，视觉重心）、以及用于
        # 精确测量文本宽度的 Font 对象（垂直模式需要水平居中）。
        self._font = (self.cfg.font_family, self.cfg.font_size)
        self._font_bold = (self.cfg.font_family, self.cfg.font_size, "bold")
        self._fm = tkfont.Font(family=self.cfg.font_family, size=self.cfg.font_size)
        self._fm_bold = tkfont.Font(
            family=self.cfg.font_family, size=self.cfg.font_size, weight="bold"
        )

        # 垂直轮播模式的状态：当前展示第几只、切换动画的纵向像素偏移。
        self._v_index = 0
        self._v_scroll = 0

        # 支持拖动（非嵌入模式下方便挪位置）+ 右键弹出上下文菜单
        self._drag = {"x": 0, "y": 0}
        # 会话内权威窗口坐标：拖动结束写回，"复原"清空回默认。
        # 初值取配置中上次持久化的坐标（-1 视为未设置）。
        self._last_pos_lock = threading.Lock()
        if self.cfg.pos_x >= 0 and self.cfg.pos_y >= 0:
            self._last_pos: tuple[int, int] | None = (self.cfg.pos_x, self.cfg.pos_y)
        else:
            self._last_pos = None
        # 线程安全的"复原到默认位置"请求：由托盘线程置位，Tk 线程轮询执行。
        self._reset_pos = threading.Event()
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda e: self._on_right_click(e))

        # 右键上下文菜单：给出显式操作，避免"右键即消失"的意外行为。
        self._menu = tk.Menu(self.root, tearoff=0)
        self._topmost_var = tk.BooleanVar(value=bool(self.cfg.always_on_top))
        self._menu.add_checkbutton(
            label="置顶显示",
            variable=self._topmost_var,
            command=self._toggle_always_on_top,
        )
        self._menu.add_separator()
        if self._managed:
            self._menu.add_command(label="隐藏悬浮条", command=self.hide)
        else:
            self._menu.add_command(label="退出", command=self.stop)

        self._embedded = False
        self._position_window()

    # ---------- 窗口定位 ----------
    def _position_window(self) -> None:
        self.root.update_idletasks()
        embedded = False
        if self.cfg.embed_taskbar:
            hwnd = self._get_hwnd()
            if hwnd and taskbar.embed_into_taskbar(hwnd):
                taskbar.place_in_taskbar(hwnd, self.cfg.width, self.cfg.height)
                embedded = True
        self._embedded = embedded

        if not embedded:
            # 降级：贴屏幕右下角悬浮 —— 若有持久化坐标则优先恢复到上次位置。
            x, y = self._resolve_position()
            self.root.geometry(f"{self.cfg.width}x{self.cfg.height}+{x}+{y}")
            # overrideredirect 窗口需要强制刷新才会真正绘制到目标位置
            self.root.update_idletasks()
            self.root.lift()
            if self.cfg.always_on_top:
                self.root.attributes("-topmost", True)

    def _default_position(self) -> tuple[int, int]:
        """默认落点：贴屏幕右下角（按 margin 偏移）。

        DPI-aware 进程下 tkinter 的 winfo_screenwidth() 可能仍返回逻辑(缩放)
        像素，而 geometry() 使用物理像素，二者混用会导致定位偏移。因此优先
        用 Win32 物理分辨率，取不到再回退到 tkinter。
        """
        sw = sh = None
        try:
            import ctypes

            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            sh = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        except Exception:  # noqa: BLE001
            sw = sh = None
        if not sw or not sh:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        x = max(0, sw - self.cfg.width - self.cfg.margin_right)
        y = max(0, sh - self.cfg.height - self.cfg.margin_bottom)
        return x, y

    def _resolve_position(self) -> tuple[int, int]:
        """确定窗口应落在的坐标：有会话内权威坐标(拖动/持久化)则复用并
        钳制到屏幕范围内，否则回退默认右下角。"""
        with self._last_pos_lock:
            saved = self._last_pos
        if saved is None:
            return self._default_position()
        # 钳制：避免因换屏幕/改分辨率导致窗口落到不可见区域找不到。
        sw = sh = None
        try:
            import ctypes

            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
        except Exception:  # noqa: BLE001
            sw = sh = None
        if not sw or not sh:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        x = min(max(0, saved[0]), max(0, sw - self.cfg.width))
        y = min(max(0, saved[1]), max(0, sh - self.cfg.height))
        return x, y

    def _get_hwnd(self) -> int | None:
        """返回真正的顶层窗口句柄。

        注意：``winfo_id()`` 在 Windows 上返回的是 tkinter 的内部子窗口
        (TkChild)，并非可见的顶层窗口 (TkTopLevel)。必须用 GetAncestor
        取根窗口，否则 SetParent/MoveWindow 都会作用在错误的句柄上。
        """
        try:
            child = int(self.root.winfo_id())
        except Exception:  # noqa: BLE001
            return None
        try:
            import win32gui  # 延迟导入，非 Windows 环境不依赖

            GA_ROOT = 2
            root_hwnd = win32gui.GetAncestor(child, GA_ROOT)
            return int(root_hwnd) if root_hwnd else child
        except Exception:  # noqa: BLE001
            # 拿不到就退回子窗口句柄（至少不比原来差）
            return child

    # ---------- 拖动 ----------
    def _on_press(self, event: "tk.Event") -> None:
        self._drag["x"] = event.x
        self._drag["y"] = event.y

    def _on_drag(self, event: "tk.Event") -> None:
        if self._embedded:
            return  # 嵌入任务栏时不允许拖动
        x = self.root.winfo_x() + event.x - self._drag["x"]
        y = self.root.winfo_y() + event.y - self._drag["y"]
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, event: "tk.Event") -> None:
        """拖动结束：记录窗口当前绝对坐标并写回配置，实现重启后位置保持。"""
        if self._embedded:
            return
        try:
            x = int(self.root.winfo_x())
            y = int(self.root.winfo_y())
        except Exception:  # noqa: BLE001
            return
        self.cfg.pos_x = x
        self.cfg.pos_y = y
        with self._last_pos_lock:
            self._last_pos = (x, y)
        self._persist_config()

    def get_position(self) -> "tuple[int, int] | None":
        """线程安全返回会话内权威坐标（未拖动/已复原时为 None）。

        供托盘在重建窗口(update_config)时回填，避免设置对话框用旧坐标覆盖。
        """
        with self._last_pos_lock:
            return self._last_pos

    def request_reset_position(self) -> None:
        """线程安全请求：把悬浮窗复原到默认右下角位置（由 Tk 线程执行）。"""
        self._reset_pos.set()

    def _do_reset_position(self) -> None:
        """在 Tk 线程内执行复原：清空持久化坐标 → 回到默认右下角 → 落盘。"""
        self.cfg.pos_x = -1
        self.cfg.pos_y = -1
        with self._last_pos_lock:
            self._last_pos = None
        if not self._embedded:
            x, y = self._default_position()
            try:
                self.root.geometry(f"{self.cfg.width}x{self.cfg.height}+{x}+{y}")
                self.root.update_idletasks()
                self.root.lift()
            except Exception:  # noqa: BLE001
                pass
        self._persist_config()

    def _persist_config(self) -> None:
        """把当前配置写回磁盘（已知配置路径时用该路径，否则用默认名）。"""
        try:
            if getattr(self, "_config_path", None):
                self.cfg.save(self._config_path)
            else:
                self.cfg.save()
        except Exception:  # noqa: BLE001
            logger.exception("持久化配置失败")

    # ---------- 行情刷新线程 ----------
    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            quotes = fetch_quotes(self.cfg.symbols)
            if quotes:
                with self._lock:
                    self._quotes = quotes
            # 用 wait 而非 sleep，stop 时能立即退出
            self._stop.wait(self.cfg.refresh_interval)

    def _trend_style(self, q: Quote) -> tuple[str, str]:
        """按涨跌返回 (颜色, 方向箭头前缀)。A 股习惯：红涨绿跌。"""
        if q.is_up:
            return self.cfg.up_color, "▲ "
        if q.is_down:
            return self.cfg.down_color, "▼ "
        return self.cfg.flat_color, "● "

    def _draw_quote_inline(self, x: int, y: int, q: Quote) -> int:
        """在 (x, y) 左对齐绘制一只股票（名称+现价+箭头涨跌幅），
        返回绘制结束时的 x 坐标（供横向排布累计宽度）。"""
        color, arrow = self._trend_style(q)
        sign = "+" if q.change_pct > 0 else ""
        name = q.name
        price = f"{q.price:.2f}"
        pct = f"{arrow}{sign}{q.change_pct:.2f}%"
        self.canvas.create_text(
            x, y, text=name, fill=self._muted, font=self._font, anchor="w"
        )
        x += self._fm.measure(name) + 6
        self.canvas.create_text(
            x, y, text=price, fill=self._fg, font=self._font_bold, anchor="w"
        )
        x += self._fm_bold.measure(price) + 6
        self.canvas.create_text(
            x, y, text=pct, fill=color, font=self._font, anchor="w"
        )
        x += self._fm.measure(pct)
        return x

    def _draw_quote_centered(self, cy: int, q: Quote) -> None:
        """在纵向坐标 cy 处水平居中绘制一只股票（垂直轮播用）。"""
        color, arrow = self._trend_style(q)
        sign = "+" if q.change_pct > 0 else ""
        name = q.name
        price = f"{q.price:.2f}"
        pct = f"{arrow}{sign}{q.change_pct:.2f}%"
        gap = 8
        total = (
            self._fm.measure(name)
            + gap
            + self._fm_bold.measure(price)
            + gap
            + self._fm.measure(pct)
        )
        x = max(8, (self.cfg.width - total) // 2)
        self.canvas.create_text(
            x, cy, text=name, fill=self._muted, font=self._font, anchor="w"
        )
        x += self._fm.measure(name) + gap
        self.canvas.create_text(
            x, cy, text=price, fill=self._fg, font=self._font_bold, anchor="w"
        )
        x += self._fm_bold.measure(price) + gap
        self.canvas.create_text(
            x, cy, text=pct, fill=color, font=self._font, anchor="w"
        )

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        """在 Canvas 上画一个圆角矩形（用平滑多边形近似）。"""
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_card_bg(self) -> None:
        """圆角模式下绘制卡片背景（窗口四角已被透明色抠空）。"""
        if self._transparent_mode:
            return  # 透明样式不画背景，文字直接悬浮在桌面上
        if not self._round:
            return
        r = min(self.cfg.corner_radius, self.cfg.height // 2)
        self._round_rect(
            0, 0, self.cfg.width - 1, self.cfg.height - 1,
            r, fill=self._card_bg, outline="",
        )

    # ---------- 绘制 ----------
    def _redraw(self) -> None:
        with self._lock:
            quotes = list(self._quotes)

        self.canvas.delete("all")
        self._draw_card_bg()

        if not quotes:
            self.canvas.create_text(
                self.cfg.width // 2, self.cfg.height // 2,
                text="加载行情中...", fill=self._muted,
                font=self._font, anchor="center",
            )
            return

        if self.cfg.display_mode == "vertical":
            self._redraw_vertical(quotes)
        else:
            self._redraw_horizontal(quotes)

    def _redraw_horizontal(self, quotes: list[Quote]) -> None:
        """横向跑马灯：所有股票拼成一行，从右向左匀速滚动。"""
        x = self._offset
        gap = 32  # 每只股票之间的间距
        y = self.cfg.height // 2
        for q in quotes:
            x = self._draw_quote_inline(x, y, q) + gap
        self._total_width = x - self._offset

    def _redraw_vertical(self, quotes: list[Quote]) -> None:
        """垂直轮播：一次只显示一只，切换时当前项上滑、下一项从底部滑入。"""
        n = len(quotes)
        h = self.cfg.height
        idx = self._v_index % n
        off = self._v_scroll
        # 当前项：从中线向上滑出
        self._draw_quote_centered(h // 2 - off, quotes[idx])
        # 切换动画进行中时，下一项从底部滑入
        if off > 0:
            nxt = quotes[(idx + 1) % n]
            self._draw_quote_centered(h // 2 - off + h, nxt)

    def _scroll_step(self) -> None:
        if self._stop.is_set():
            return
        self._offset -= self.cfg.scroll_speed
        self._redraw()

        # 整条文本完全滚出左边界后，从右侧重新进入，形成循环
        total = getattr(self, "_total_width", self.cfg.width)
        if self._offset < -total:
            self._offset = self.cfg.width

        self.root.after(self.cfg.scroll_fps_ms, self._scroll_step)

    # ---------- 垂直轮播动画 ----------
    def _vertical_dwell(self) -> None:
        """停留阶段：静态展示当前股票若干秒，然后进入上滑切换。"""
        if self._stop.is_set():
            return
        self._v_scroll = 0
        self._redraw()
        self.root.after(self.cfg.vertical_dwell_ms, self._vertical_slide)

    def _vertical_slide(self) -> None:
        """切换阶段：当前项向上滚出、下一项滑入，滚完切到下一只并重新停留。"""
        if self._stop.is_set():
            return
        with self._lock:
            n = len(self._quotes)
        if n <= 1:
            # 只有 0/1 只股票，无需切换，继续停留
            self._redraw()
            self.root.after(self.cfg.vertical_dwell_ms, self._vertical_slide)
            return
        # 每帧上滚的像素数：跨越一个组件高度，用 scroll_speed 的 2 倍略快些
        self._v_scroll += max(2, self.cfg.scroll_speed * 2)
        if self._v_scroll >= self.cfg.height:
            # 切换完成：定位到下一只，回到停留阶段
            self._v_scroll = 0
            self._v_index = (self._v_index + 1) % n
            self._vertical_dwell()
            return
        self._redraw()
        self.root.after(self.cfg.scroll_fps_ms, self._vertical_slide)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        if self.cfg.display_mode == "vertical":
            self._v_index = 0
            self._v_scroll = 0
            self._vertical_dwell()
        else:
            self._offset = self.cfg.width  # 从右侧进入
            self._scroll_step()
        # 托管模式：关闭按钮隐藏而非退出；启动一个可见性轮询循环，
        # 让后台线程（热键/托盘菜单）能安全地控制窗口显隐。
        if self._managed:
            self.root.protocol("WM_DELETE_WINDOW", self.hide)
            if not self.cfg.float_on_start:
                self.root.withdraw()
                self._is_visible = False
            self._poll_visibility()
        else:
            self.root.protocol("WM_DELETE_WINDOW", self.stop)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.stop()
        finally:
            # mainloop 退出后仍在本 (Tk) 线程内。self.root 持有的 tk.Tk()
            # 对象封装着 Tcl 解释器与 async handler；Tk.destroy() 只销毁窗口
            # 部件，并不释放该解释器。若此处不主动断链，self.root（及其与本
            # widget 之间的引用环）要等 Python 的循环垃圾回收器回收，而那可能
            # 发生在主线程，届时 Tcl_DeleteInterp/Tcl_AsyncDelete 在错误线程
            # 执行，触发 `async handler deleted by the wrong thread` 崩溃。
            # 在解释器所属的 Tk 线程内清引用并 gc.collect()，把析构钉死在正确线程。
            self._cleanup_tk_objects()
            try:
                self.root = None
            except Exception:  # noqa: BLE001
                pass
            try:
                gc.collect()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 显隐控制（线程安全）----------
    def _toggle_always_on_top(self) -> None:
        """右键菜单切换"置顶显示"：即时应用/取消置顶，无需重启。
        并把改动写回配置文件，让独立进程的设置对话框保持同步。"""
        want = bool(self._topmost_var.get())
        self.cfg.always_on_top = want
        try:
            self.root.attributes("-topmost", want)
            if want:
                # 立即抢回顶层（_reassert_topmost 内部已受 always_on_top 门控）。
                self.root.lift()
                self._reassert_topmost()
        except Exception:  # noqa: BLE001
            pass
        # 落盘同步：设置对话框是独立进程、从磁盘读配置，不写盘就不会同步。
        try:
            if self._config_path:
                self.cfg.save(self._config_path)
            else:
                self.cfg.save()
        except Exception:  # noqa: BLE001
            pass

    def _on_right_click(self, event=None) -> None:
        """右键：弹出上下文菜单（显式操作），避免"右键即消失"的意外行为。

        菜单默认锚点在光标右下方，容易被悬浮窗自身遮住。这里把菜单弹到
        光标的正上方（按菜单实际高度上移），避开悬浮条区域。
        """
        try:
            if event is not None:
                x, y = event.x_root, event.y_root
            else:
                x = self.root.winfo_pointerx()
                y = self.root.winfo_pointery()
            # 计算菜单高度，将菜单整体上移到光标上方，避免被悬浮窗遮挡。
            try:
                self._menu.update_idletasks()
                menu_h = self._menu.winfo_reqheight()
            except Exception:  # noqa: BLE001
                menu_h = 0
            popup_y = y - menu_h
            if popup_y < 0:
                popup_y = y
            self._menu.tk_popup(x, popup_y)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                self._menu.grab_release()
            except Exception:  # noqa: BLE001
                pass

    def show(self) -> None:
        """请求显示窗口（可从任意线程调用）。"""
        self._want_visible.set()

    def hide(self) -> None:
        """请求隐藏窗口（可从任意线程调用）。"""
        self._want_visible.clear()

    def toggle(self) -> None:
        """切换显示/隐藏（可从任意线程调用）。"""
        if self._want_visible.is_set():
            self._want_visible.clear()
        else:
            self._want_visible.set()

    def is_visible(self) -> bool:
        return self._want_visible.is_set()

    def _reassert_topmost(self) -> None:
        """重新把窗口顶到最前（不抢焦点）。

        overrideredirect 的 ``-topmost`` 属性只在设置的那一刻把窗口拔到
        Z 序最前，之后若用户激活了别的置顶窗口/全屏程序，本窗会被压到
        后面且 tkinter 不会自动恢复。故在可见轮询里用 SetWindowPos 周期
        性重申 HWND_TOPMOST；带 SWP_NOACTIVATE 保证不抢走用户焦点，带
        NOMOVE/NOSIZE 保证不动位置尺寸。嵌入任务栏时窗口已被 reparent，
        不应再强制置顶，故跳过。
        """
        if self._embedded:
            return
        if not self.cfg.always_on_top:
            # 用户关闭了"始终置顶"：不再强制抢占 Z 序，允许被其它窗口遮挡。
            return
        hwnd = self._get_hwnd()
        if not hwnd:
            return
        try:
            import win32gui
            import win32con

            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )
        except Exception:  # noqa: BLE001
            # 非 Windows 或缺 pywin32：退回 tk 的 topmost 属性重申
            try:
                self.root.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass

    def _poll_visibility(self) -> None:
        """在 Tk 线程内周期性地把期望可见状态同步到实际窗口状态。"""
        if self._stop.is_set():
            # 收到停止请求：在 Tk 线程内安全销毁窗口，结束 mainloop。
            # （跨线程直接调用 root.destroy 不安全，故统一在此执行。）
            self._cleanup_tk_objects()
            try:
                self.root.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        want = self._want_visible.is_set()
        if want != self._is_visible:
            try:
                if want:
                    self.root.deiconify()
                    self.root.lift()
                    # 无边框(overrideredirect)窗口 withdraw 后再 deiconify 会丢失
                    # Z 序，仅 lift 在部分环境下无法可靠浮到前台，导致"看不见"。
                    # 用一次 -topmost 脉冲强制把窗口拔到最前；若用户未开启"始终
                    # 置顶"，立即取消 topmost，使其恢复为可被遮挡的普通窗口。
                    self.root.attributes("-topmost", True)
                    if not self.cfg.always_on_top:
                        self.root.attributes("-topmost", False)
                else:
                    self.root.withdraw()
                self._is_visible = want
            except Exception:  # noqa: BLE001
                pass
        # 稳态下也要周期性重申置顶：切换其它窗口后本窗会被抢占沉底，
        # 仅靠状态切换时设置一次 -topmost 无法维持。
        if self._is_visible:
            self._reassert_topmost()
        # 复原请求：由外部线程(托盘/热键)置位，几何操作必须回到 Tk 线程执行。
        if self._reset_pos.is_set():
            self._reset_pos.clear()
            try:
                self._do_reset_position()
            except Exception:  # noqa: BLE001
                logger.exception("复原窗口位置失败")
        self.root.after(150, self._poll_visibility)

    def stop(self) -> None:
        self._stop.set()
        self._cleanup_tk_objects()
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _cleanup_tk_objects(self) -> None:
        """在 Tk 线程内、销毁 root 之前，主动释放本实例持有的 tk.Variable /
        tk.Menu 等对象引用，并立即触发一次垃圾回收。

        根因：每个 FloatWidget 实例都创建独立的 tk.Tk() 解释器，其上挂着
        tk.BooleanVar(_topmost_var) 等 Variable。若不在此清理，这些对象会在
        实例被主线程 GC 时才执行 __del__，而彼时其所属的 Tk 解释器已随本
        (Tk) 线程结束而销毁，跨线程回收句柄触发
        `Tcl_AsyncDelete: async handler deleted by the wrong thread` 崩溃。
        在解释器仍存活的 Tk 线程内先解引用并 gc.collect()，可让 __del__ 在
        正确线程、正确时机执行。
        """
        try:
            self._topmost_var = None
            self._menu = None
            # ★关键：Canvas 部件内部持有 .tk（指向本实例的 Tcl 解释器 Tkapp）。
            # 若此处不断开，即便 root/_menu/_topmost_var 都已置空，Tkapp 仍被
            # self.canvas.tk 钉住存活。重建流程里旧 widget 只剩主线程
            # FloatController.update_config 的局部变量间接引用；该函数返回、
            # 局部引用释放时，旧 widget → canvas → canvas.tk(Tkapp) 会在
            # **主线程** 触发 __del__ / Tcl_DeleteInterp，而该解释器创建于
            # Tk 线程，跨线程析构即 `Tcl_AsyncDelete: async handler deleted
            # by the wrong thread` 崩溃。在此把 canvas 也解引用，令全部 Tkapp
            # 引用在解释器所属的 Tk 线程内断链，随后的 gc.collect() 把析构钉死
            # 在正确线程。
            self.canvas = None
        except Exception:  # noqa: BLE001
            pass
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

    def request_stop(self) -> None:
        """线程安全的停止请求：仅置位事件，实际销毁交由 Tk 线程内的
        可见性轮询执行。适用于从托盘/热键等外部线程关闭悬浮窗。"""
        self._stop.set()
