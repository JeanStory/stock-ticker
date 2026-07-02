"""设置窗口（tkinter）。

右键托盘菜单点"设置…"后打开。用分页（Notebook）组织，避免竖向过长:

* 数据源: 类型(tencent/sina/custom) + 地址 URL + API Key
* 观察股票: 列表增删（支持多选删除）
* 显示: 刷新频率 + 展示方式 + 每只停留时长 + 背景样式
* 悬浮窗: 开关快捷键 + 启动自动显示

保存时写回 config.json 并回调通知托盘热应用（无需重启）。

线程模型要点（重要）:
* tray 在 daemon 线程里调用 ``open_settings``，tkinter 的 mainloop 也在该
  线程跑。窗口关闭时若直接 ``root.destroy()``，那些 ``StringVar``/``BooleanVar``
  的 Python 对象仍被局部闭包引用，等进程主线程的 GC 触发它们的 ``__del__``
  时，Tcl 解释器已随窗口销毁 → 抛 "main thread is not in main loop"。
* 修法: 关闭时只 ``root.quit()`` 退出 mainloop（解释器仍存活），回到本
  （owner）线程后显式解除所有 tk 变量引用并 ``gc.collect()``，让终结器在
  解释器尚存活、且就在本线程内执行，最后再 ``destroy()``。
"""

from __future__ import annotations

import gc
import logging
from typing import Callable

import tkinter as tk
from tkinter import messagebox, ttk

from .config import Config, DEFAULT_CONFIG_NAME

logger = logging.getLogger(__name__)

# 数据源类型下拉可选项：展示名 -> 存储值
SOURCE_TYPES = [
    ("腾讯（默认，免费）", "tencent"),
    ("新浪（免费）", "sina"),
    ("自定义（填写下方地址）", "custom"),
]

# 悬浮窗展示方式：展示名 -> 存储值
DISPLAY_MODES = [
    ("横向滚动（跑马灯）", "horizontal"),
    ("上下轮播（逐只切换）", "vertical"),
]

# 悬浮窗背景样式：展示名 -> 存储值
BG_STYLES = [
    ("暗黑（默认）", "dark"),
    ("珍珠白", "pearl"),
    ("透明（仅文字悬浮）", "transparent"),
]

MUTED = "#888"


def open_settings(
    cfg: Config,
    config_path: str | None,
    on_saved: Callable[[Config], None] | None = None,
) -> bool:
    """打开设置窗口并阻塞到窗口关闭。

    :param cfg: 当前配置（作为初值填入表单）
    :param config_path: 配置文件路径；None 时用默认名
    :param on_saved: 可选的保存成功回调，参数为更新后的 Config。子进程
        模式下无需回调（父进程读盘热应用），仅同进程直调时才用。
    :return: 是否点了"保存"并成功写盘（供子进程模式设置退出码）
    """
    path = config_path or DEFAULT_CONFIG_NAME

    root = tk.Tk()
    root.title("股票行情 · 设置")
    root.geometry("480x400")
    root.minsize(460, 360)
    root.resizable(True, True)

    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    pad = {"padx": 10, "pady": 8}

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, **pad)

    # 收集所有 tk 变量，关闭时统一清理（见模块 docstring 的线程说明）
    tk_vars: list[tk.Variable] = []

    def _var(v: tk.Variable) -> tk.Variable:
        tk_vars.append(v)
        return v

    # ============ 页1: 数据源 ============================================
    tab_src = ttk.Frame(notebook)
    notebook.add(tab_src, text="数据源")

    ttk.Label(tab_src, text="类型").grid(row=0, column=0, sticky="w", padx=10, pady=8)
    type_var = _var(tk.StringVar())
    type_labels = [label for label, _ in SOURCE_TYPES]
    type_combo = ttk.Combobox(
        tab_src, textvariable=type_var, values=type_labels, state="readonly", width=28
    )
    type_combo.grid(row=0, column=1, sticky="w", padx=10, pady=8)
    cur_label = next(
        (label for label, val in SOURCE_TYPES if val == cfg.source_type),
        type_labels[0],
    )
    type_var.set(cur_label)

    ttk.Label(tab_src, text="地址 URL").grid(row=1, column=0, sticky="w", padx=10, pady=8)
    url_var = _var(tk.StringVar(value=cfg.source_url))
    ttk.Entry(tab_src, textvariable=url_var, width=30).grid(
        row=1, column=1, sticky="w", padx=10, pady=8
    )

    ttk.Label(tab_src, text="API Key").grid(row=2, column=0, sticky="w", padx=10, pady=8)
    key_var = _var(tk.StringVar(value=cfg.api_key))
    ttk.Entry(tab_src, textvariable=key_var, width=30, show="*").grid(
        row=2, column=1, sticky="w", padx=10, pady=8
    )

    ttk.Label(
        tab_src,
        text="内置源(腾讯/新浪)地址留空即用默认；自定义源必填地址。",
        foreground=MUTED,
        wraplength=420,
    ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 8))

    # ============ 页2: 观察股票 ==========================================
    tab_sym = ttk.Frame(notebook)
    notebook.add(tab_sym, text="观察股票")

    ttk.Label(
        tab_sym,
        text="代码如 sh600519 / sz000001 / hk00700",
        foreground=MUTED,
    ).pack(anchor="w", padx=10, pady=(8, 2))

    sym_body = ttk.Frame(tab_sym)
    sym_body.pack(fill="both", expand=True, padx=10, pady=4)

    listbox = tk.Listbox(sym_body, height=7, selectmode="extended")
    listbox.pack(side="left", fill="both", expand=True)
    for s in cfg.symbols:
        listbox.insert("end", s)

    sb = ttk.Scrollbar(sym_body, orient="vertical", command=listbox.yview)
    sb.pack(side="left", fill="y")
    listbox.config(yscrollcommand=sb.set)

    btns = ttk.Frame(sym_body)
    btns.pack(side="left", fill="y", padx=(8, 0))

    add_var = _var(tk.StringVar())
    add_entry = ttk.Entry(btns, textvariable=add_var, width=14)
    add_entry.pack(pady=(0, 4))

    def _add_symbol() -> None:
        code = add_var.get().strip()
        if not code:
            return
        existing = set(listbox.get(0, "end"))
        if code in existing:
            messagebox.showinfo("提示", f"{code} 已在列表中", parent=root)
            return
        listbox.insert("end", code)
        add_var.set("")

    def _del_symbol() -> None:
        sel = listbox.curselection()
        for idx in reversed(sel):
            listbox.delete(idx)

    add_entry.bind("<Return>", lambda _e: _add_symbol())
    ttk.Button(btns, text="添加", command=_add_symbol).pack(fill="x", pady=2)
    ttk.Button(btns, text="删除选中", command=_del_symbol).pack(fill="x", pady=2)

    # ============ 页3: 显示 ==============================================
    tab_disp = ttk.Frame(notebook)
    notebook.add(tab_disp, text="显示")

    ttk.Label(tab_disp, text="刷新频率（秒）").grid(
        row=0, column=0, sticky="w", padx=10, pady=8
    )
    freq_var = _var(tk.StringVar(value=str(cfg.refresh_interval)))
    ttk.Spinbox(tab_disp, from_=1, to=3600, textvariable=freq_var, width=8).grid(
        row=0, column=1, sticky="w", padx=10, pady=8
    )

    ttk.Label(tab_disp, text="展示方式").grid(row=1, column=0, sticky="w", padx=10, pady=8)
    mode_var = _var(tk.StringVar())
    mode_labels = [label for label, _ in DISPLAY_MODES]
    ttk.Combobox(
        tab_disp, textvariable=mode_var, values=mode_labels, state="readonly", width=22
    ).grid(row=1, column=1, sticky="w", padx=10, pady=8)
    cur_mode_label = next(
        (label for label, val in DISPLAY_MODES if val == cfg.display_mode),
        mode_labels[0],
    )
    mode_var.set(cur_mode_label)

    ttk.Label(tab_disp, text="每只停留（秒）").grid(
        row=2, column=0, sticky="w", padx=10, pady=8
    )
    dwell_var = _var(tk.StringVar(value=str(cfg.vertical_dwell_ms / 1000)))
    ttk.Spinbox(tab_disp, from_=1, to=60, increment=1, textvariable=dwell_var, width=8).grid(
        row=2, column=1, sticky="w", padx=10, pady=8
    )

    ttk.Label(tab_disp, text="背景样式").grid(row=3, column=0, sticky="w", padx=10, pady=8)
    bg_var = _var(tk.StringVar())
    bg_labels = [label for label, _ in BG_STYLES]
    ttk.Combobox(
        tab_disp, textvariable=bg_var, values=bg_labels, state="readonly", width=22
    ).grid(row=3, column=1, sticky="w", padx=10, pady=8)
    cur_bg_label = next(
        (label for label, val in BG_STYLES if val == cfg.bg_style),
        bg_labels[0],
    )
    bg_var.set(cur_bg_label)

    ttk.Label(
        tab_disp,
        text="“上下轮播”按每只停留时长切换；“横向滚动”忽略停留时长。"
        "“透明”仅悬浮文字、无底色，文字颜色随底色自动适配。",
        foreground=MUTED,
        wraplength=440,
    ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 8))

    # ============ 页4: 悬浮窗 ============================================
    tab_float = ttk.Frame(notebook)
    notebook.add(tab_float, text="悬浮窗")

    ttk.Label(tab_float, text="开关快捷键").grid(
        row=0, column=0, sticky="w", padx=10, pady=8
    )
    hotkey_var = _var(tk.StringVar(value=cfg.hotkey))
    ttk.Entry(tab_float, textvariable=hotkey_var, width=22).grid(
        row=0, column=1, sticky="w", padx=10, pady=8
    )

    ttk.Label(
        tab_float,
        text="格式如 ctrl+alt+s（支持 ctrl/alt/shift/win + 字母或数字）。",
        foreground=MUTED,
        wraplength=440,
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 6))

    float_start_var = _var(tk.BooleanVar(value=cfg.float_on_start))
    ttk.Checkbutton(
        tab_float,
        text="启动时自动显示悬浮窗",
        variable=float_start_var,
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

    auto_start_var = _var(tk.BooleanVar(value=cfg.auto_start))
    ttk.Checkbutton(
        tab_float,
        text="开机自启动（随 Windows 登录自动运行）",
        variable=auto_start_var,
    ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

    ttk.Label(
        tab_float,
        text="开机自启写入当前用户注册表，无需管理员权限；关闭后自动移除。",
        foreground=MUTED,
        wraplength=440,
    ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

    always_on_top_var = _var(tk.BooleanVar(value=cfg.always_on_top))
    ttk.Checkbutton(
        tab_float,
        text="始终置顶（悬浮窗保持在其它窗口之上）",
        variable=always_on_top_var,
    ).grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

    ttk.Label(
        tab_float,
        text="关闭后悬浮窗不再强制抢占顶层，可被其它窗口正常遮挡。",
        foreground=MUTED,
        wraplength=440,
    ).grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

    # ============ 底部按钮 ===============================================
    # saved 标志：用于 mainloop 退出后是否触发保存回调（回调放到 owner 线程
    # 做，避免在 Tcl 事件回调里再销毁窗口引发时序问题）
    result = {"save": False}

    def _validate_and_stage() -> bool:
        """校验并把表单值写入 cfg（暂存），成功返回 True。不在此处关窗。"""
        try:
            interval = int(freq_var.get())
            if interval < 1:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showerror("配置错误", "刷新频率必须为正整数（秒）", parent=root)
            return False

        symbols = [s.strip() for s in listbox.get(0, "end") if s.strip()]
        if not symbols:
            messagebox.showerror("配置错误", "至少保留一只观察股票", parent=root)
            return False

        try:
            dwell_sec = float(dwell_var.get())
            if dwell_sec < 1:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showerror(
                "配置错误", "每只停留时长必须为不小于 1 的数（秒）", parent=root
            )
            return False

        dmode = next(
            (val for label, val in DISPLAY_MODES if label == mode_var.get()),
            "horizontal",
        )
        bstyle = next(
            (val for label, val in BG_STYLES if label == bg_var.get()),
            "dark",
        )
        stype = next(
            (val for label, val in SOURCE_TYPES if label == type_var.get()),
            "tencent",
        )
        url = url_var.get().strip()
        if stype == "custom" and not url:
            messagebox.showerror("配置错误", "自定义数据源必须填写地址 URL", parent=root)
            return False

        hotkey = hotkey_var.get().strip()
        if not hotkey:
            messagebox.showerror("配置错误", "悬浮窗开关快捷键不能为空", parent=root)
            return False
        try:
            from .hotkey import parse_hotkey

            parse_hotkey(hotkey)
        except Exception:  # noqa: BLE001 - 解析失败即视为非法组合
            messagebox.showerror(
                "配置错误",
                f"无法识别快捷键「{hotkey}」，格式如 ctrl+alt+s",
                parent=root,
            )
            return False

        cfg.source_type = stype
        cfg.source_url = url
        cfg.api_key = key_var.get().strip()
        cfg.symbols = symbols
        cfg.refresh_interval = interval
        cfg.hotkey = hotkey
        cfg.float_on_start = bool(float_start_var.get())
        cfg.auto_start = bool(auto_start_var.get())
        cfg.always_on_top = bool(always_on_top_var.get())
        cfg.display_mode = dmode
        cfg.vertical_dwell_ms = int(dwell_sec * 1000)
        cfg.bg_style = bstyle
        return True

    def _save() -> None:
        if not _validate_and_stage():
            return
        cfg.save(path)
        logger.info("设置已保存到 %s", path)
        # 按开关同步开机自启（写入/移除当前用户注册表 Run 键）。
        from . import autostart

        if not autostart.sync(cfg.auto_start, path):
            messagebox.showwarning(
                "开机自启未生效",
                "配置已保存，但同步开机自启到系统时失败，请查看日志。",
                parent=root,
            )
        messagebox.showinfo("已保存", "设置已保存并生效。", parent=root)
        result["save"] = True
        root.quit()  # 退出 mainloop，解释器留给 owner 线程收尾

    def _cancel() -> None:
        root.quit()

    root.protocol("WM_DELETE_WINDOW", _cancel)

    bottom = ttk.Frame(root)
    bottom.pack(fill="x", **pad)
    ttk.Button(bottom, text="保存", command=_save).pack(side="right", padx=8)
    ttk.Button(bottom, text="取消", command=_cancel).pack(side="right")

    root.mainloop()

    # ---- mainloop 已退出 ----
    # 子进程模式下 on_saved 为 None，父进程读盘热应用；同进程直调时才回调。
    if result["save"] and on_saved is not None:
        try:
            on_saved(cfg)
        except Exception as exc:  # noqa: BLE001 - 回调异常不应阻塞关窗
            logger.warning("保存回调异常: %s", exc)

    # 销毁窗口。子进程模式下本函数运行在真正的主线程里，进程随后退出，
    # Tcl 解释器与所有 tk 变量由进程回收，无需手动 del/gc 规避跨线程终结。
    try:
        root.destroy()
    except tk.TclError:
        pass

    return result["save"]
