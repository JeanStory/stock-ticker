"""全局快捷键监听。

基于 Windows 原生 ``RegisterHotKey`` + 消息循环实现，无需额外第三方热键库
（项目已依赖 ``pywin32``）。监听在独立守护线程里跑自己的消息循环，
命中热键时回调主逻辑切换悬浮窗显示状态。

仅支持 Windows；非 Windows 平台下 :func:`start` 会直接返回一个空操作的
监听器，保证跨平台导入不报错。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------- 修饰键常量（与 WinUser.h 一致）----------
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# 让系统忽略键盘自动重复触发，避免长按时反复切换
MOD_NOREPEAT = 0x4000

_WM_HOTKEY = 0x0312

_MOD_ALIASES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}


def _vk_for_key(key: str) -> Optional[int]:
    """把单个按键名解析成虚拟键码 VK。支持 a-z / 0-9 / F1-F12。"""
    key = key.strip().lower()
    if not key:
        return None
    # 字母、数字：VK 码与大写 ASCII 一致
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return ord(key.upper())
    # 功能键 F1-F12：VK_F1 = 0x70
    if key.startswith("f") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 12:
            return 0x70 + (n - 1)
    return None


def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
    """把 ``"ctrl+alt+s"`` 解析成 ``(modifiers, vk)``。

    解析失败（空串、无按键、未知键名）返回 ``None``。
    """
    if not spec or not spec.strip():
        return None
    mods = 0
    vk: Optional[int] = None
    for part in spec.split("+"):
        token = part.strip().lower()
        if not token:
            continue
        if token in _MOD_ALIASES:
            mods |= _MOD_ALIASES[token]
        else:
            resolved = _vk_for_key(token)
            if resolved is None:
                logger.warning("无法识别的快捷键按键: %r（完整配置: %r）", token, spec)
                return None
            vk = resolved
    if vk is None:
        logger.warning("快捷键缺少主按键: %r", spec)
        return None
    return mods | MOD_NOREPEAT, vk


class HotkeyListener:
    """在独立线程里监听单个全局快捷键。"""

    def __init__(self, spec: str, on_trigger: Callable[[], None]) -> None:
        self._spec = spec
        self._on_trigger = on_trigger
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._stop = threading.Event()

    def start(self) -> bool:
        """启动监听线程。解析失败或非 Windows 平台返回 ``False``。"""
        parsed = parse_hotkey(self._spec)
        if parsed is None:
            return False
        try:
            import win32con  # noqa: F401  # 仅用于确认 pywin32 可用
        except ImportError:
            logger.warning("未安装 pywin32，全局快捷键不可用")
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=parsed, daemon=True, name="hotkey-listener"
        )
        self._thread.start()
        return True

    def _run(self, mods: int, vk: int) -> None:
        import win32api
        import win32gui

        self._thread_id = win32api.GetCurrentThreadId()
        hotkey_id = 1
        if not self._register(hotkey_id, mods, vk):
            return
        logger.info("已注册全局快捷键: %s", self._spec)
        try:
            # GetMessage 阻塞取消息；命中 WM_HOTKEY 就触发回调。
            # PostThreadMessage(WM_QUIT) 会让 GetMessage 返回 0 从而退出。
            while not self._stop.is_set():
                got, msg = win32gui.GetMessage(None, 0, 0)
                if got == 0 or got == -1:  # WM_QUIT / 错误
                    break
                # msg 结构: (hwnd, message, wparam, lparam, time, point)
                if msg[1] == _WM_HOTKEY:
                    try:
                        self._on_trigger()
                    except Exception:  # noqa: BLE001
                        logger.exception("快捷键回调执行出错")
        finally:
            self._unregister(hotkey_id)

    @staticmethod
    def _register(hotkey_id: int, mods: int, vk: int) -> bool:
        import win32gui

        try:
            win32gui.RegisterHotKey(None, hotkey_id, mods, vk)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("注册全局快捷键失败（可能已被其他程序占用）")
            return False

    @staticmethod
    def _unregister(hotkey_id: int) -> None:
        import win32gui

        try:
            win32gui.UnregisterHotKey(None, hotkey_id)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        """停止监听并让消息循环线程退出。"""
        self._stop.set()
        if self._thread_id is not None:
            try:
                import win32api
                import win32con

                # 唤醒阻塞在 GetMessage 的线程，令其检查停止标志并退出
                win32api.PostThreadMessage(self._thread_id, win32con.WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
