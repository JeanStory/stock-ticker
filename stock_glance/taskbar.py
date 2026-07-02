"""Windows 任务栏嵌入（可选能力）。

通过 win32 API 把 Tk 窗口 SetParent 到任务栏的 ``TrayNotifyWnd`` 区域，
实现真正意义上的“任务栏小组件”。这依赖 pywin32，且行为随 Windows 版本
略有差异，因此全部做成软失败：任何异常都返回 False，由调用方降级为
贴屏底部的悬浮窗。

仅在 Windows 上生效，其它平台直接返回 False。
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith("win")

try:
    import win32gui  # type: ignore
    import win32con  # type: ignore

    _HAS_PYWIN32 = True
except Exception:  # noqa: BLE001 - 未安装 pywin32 也能跑（降级模式）
    _HAS_PYWIN32 = False


def get_taskbar_geometry() -> tuple[int, int, int, int] | None:
    """返回任务栏矩形 (left, top, right, bottom)，失败返回 None。"""
    if not (IS_WINDOWS and _HAS_PYWIN32):
        return None
    try:
        hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        if not hwnd:
            return None
        return win32gui.GetWindowRect(hwnd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取任务栏几何信息失败: %s", exc)
        return None


def embed_into_taskbar(hwnd: int) -> bool:
    """把给定窗口句柄嵌入任务栏。成功返回 True。

    做法是将窗口的父窗口设为 Shell_TrayWnd。设置为子窗口后需要清掉顶层
    窗口样式，避免出现边框/在任务栏留下按钮。

    注意：现代 Windows（尤其 Win11）的任务栏是 UWP/XAML 实现，SetParent
    嵌入外部窗口通常无效。因此这里在设置后会**验证父窗口是否真的改变**，
    若未生效则还原样式并返回 False，交由调用方降级为悬浮窗。
    """
    if not (IS_WINDOWS and _HAS_PYWIN32):
        return False

    orig_ex_style = None
    orig_style = None
    try:
        tray = win32gui.FindWindow("Shell_TrayWnd", None)
        if not tray:
            logger.warning("未找到任务栏窗口 Shell_TrayWnd")
            return False

        # 先保存原始样式，便于失败时还原
        orig_ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        orig_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)

        # 调整扩展样式：工具窗口，不在 Alt+Tab / 任务栏出现按钮
        ex_style = (orig_ex_style | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)

        # 设置为子窗口样式
        style = (orig_style | win32con.WS_CHILD) & ~win32con.WS_POPUP
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

        win32gui.SetParent(hwnd, tray)

        # 关键：验证父窗口是否真的变成了任务栏。Win11 上通常不会生效。
        actual_parent = win32gui.GetParent(hwnd)
        if actual_parent != tray:
            logger.warning(
                "SetParent 未生效 (parent=%s, 期望=%s)，还原样式并降级为悬浮窗",
                actual_parent, tray,
            )
            # 还原样式，避免窗口停留在 WS_CHILD 状态导致不可见
            win32gui.SetParent(hwnd, 0)
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, orig_style)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, orig_ex_style)
            return False

        logger.info("已嵌入任务栏 (hwnd=%s -> tray=%s)", hwnd, tray)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("嵌入任务栏失败，将降级为悬浮窗: %s", exc)
        # 尽力还原样式
        try:
            if orig_style is not None:
                win32gui.SetParent(hwnd, 0)
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, orig_style)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, orig_ex_style)
        except Exception:  # noqa: BLE001
            pass
        return False


def place_in_taskbar(hwnd: int, width: int, height: int) -> bool:
    """把已嵌入的窗口放到任务栏内合适的位置（时间/托盘区左侧）。"""
    if not (IS_WINDOWS and _HAS_PYWIN32):
        return False
    try:
        tray = win32gui.FindWindow("Shell_TrayWnd", None)
        tray_rect = win32gui.GetWindowRect(tray)
        tb_width = tray_rect[2] - tray_rect[0]
        tb_height = tray_rect[3] - tray_rect[1]

        # 找托盘通知区，把组件放到它左边，避免遮挡时钟
        notify = win32gui.FindWindowEx(tray, 0, "TrayNotifyWnd", None)
        if notify:
            notify_rect = win32gui.GetWindowRect(notify)
            notify_width = notify_rect[2] - notify_rect[0]
            x = tb_width - notify_width - width - 8
        else:
            x = tb_width - width - 160

        y = max(0, (tb_height - height) // 2)
        win32gui.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务栏内定位失败: %s", exc)
        return False
