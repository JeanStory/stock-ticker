"""开机自启动（Windows 当前用户级别）。

实现方式:写入注册表 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
的一个命名值。选这条路径的理由:

* 仅当前用户生效，**无需管理员权限**，也不改动系统级设置；
* 登录后由 Explorer 自动拉起，行为与其它自启软件一致；
* 完全可逆——删除该键值即彻底取消，不留残余。

命令构造需处理两种发行形态:

* **PyInstaller 冻结包**(``sys.frozen``):直接用 ``sys.executable`` 自身，
  它就是打好的 exe，无需再带解释器。
* **源码运行**:用与当前解释器同目录的 ``pythonw.exe``(无控制台窗口)拉起
  ``run.py``。找不到 pythonw 时退回 ``python.exe``。

无论哪种形态,都把配置文件路径转成**绝对路径**并作为 ``-c`` 参数传入，
因为开机自启时工作目录通常是 ``system32`` 之类，相对的 ``config.json``
会找不到。
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# 注册表 Run 键下使用的值名（唯一标识本程序的自启项）
_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_VALUE_NAME = "StockTickerWidget"


def _is_windows() -> bool:
    return os.name == "nt"


def build_launch_command(config_path: str | None) -> str:
    """构造写入注册表的启动命令行字符串（各段按需加引号）。

    :param config_path: 配置文件路径；转为绝对路径后作为 ``-c`` 传入。
    """

    def q(s: str) -> str:
        # 路径含空格时必须加引号；统一都加以求稳妥
        return f'"{s}"'

    parts: list[str]
    if getattr(sys, "frozen", False):
        # 冻结包:exe 自身即入口
        parts = [q(sys.executable)]
    else:
        # 源码运行:优先用 pythonw(无黑框)拉起 run.py
        exe_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(exe_dir, "pythonw.exe")
        interp = pythonw if os.path.exists(pythonw) else sys.executable
        # run.py 位于包目录的上一级
        run_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py"
        )
        parts = [q(interp), q(run_py)]

    if config_path:
        parts += ["-c", q(os.path.abspath(config_path))]

    return " ".join(parts)


def is_enabled() -> bool:
    """当前是否已登记开机自启。"""
    if not _is_windows():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, _APP_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError as exc:  # noqa: BLE001
        logger.warning("查询自启状态失败: %s", exc)
        return False


def enable(config_path: str | None = None) -> bool:
    """登记开机自启（幂等，重复调用会刷新命令行）。返回是否成功。"""
    if not _is_windows():
        logger.warning("开机自启仅支持 Windows，当前系统已跳过。")
        return False
    import winreg

    command = build_launch_command(config_path)
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _APP_VALUE_NAME, 0, winreg.REG_SZ, command)
        logger.info("已开启开机自启: %s", command)
        return True
    except OSError as exc:  # noqa: BLE001
        logger.error("开启开机自启失败: %s", exc)
        return False


def disable() -> bool:
    """取消开机自启（幂等，未登记时也视为成功）。返回是否成功。"""
    if not _is_windows():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_VALUE_NAME)
        logger.info("已关闭开机自启。")
        return True
    except FileNotFoundError:
        # 键或值本就不存在，等价于已关闭
        return True
    except OSError as exc:  # noqa: BLE001
        logger.error("关闭开机自启失败: %s", exc)
        return False


def sync(enabled: bool, config_path: str | None = None) -> bool:
    """按目标状态对齐注册表(设置界面保存时调用)。

    :param enabled: 期望的开机自启开关
    :param config_path: 配置文件路径，写入启动命令的 ``-c`` 参数
    :return: 操作是否成功
    """
    return enable(config_path) if enabled else disable()
