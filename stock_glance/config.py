"""配置加载。

用户配置存于工作目录下的 ``config.json``。首次运行若不存在会自动生成
一份带默认值的模板，方便直接改股票代码。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "config.json"


@dataclass
class Config:
    # 要展示的股票代码列表，支持 "600519" / "sh600519" / "00700.HK" 等写法
    symbols: list[str] = field(
        default_factory=lambda: ["sh600519", "sz000001", "sh000001", "hk00700"]
    )
    # 行情刷新间隔（秒）
    refresh_interval: int = 5
    # 数据源类型: "tencent"(腾讯,默认) / "sina"(新浪) / "custom"(自定义)
    source_type: str = "tencent"
    # 自定义数据源地址。留空时按 source_type 使用内置默认地址。
    # 内置源(tencent/sina)填入则覆盖默认 URL；custom 源必填。
    # 约定: 程序会以 "<url>" + 逗号分隔的规范化代码 拼接请求。
    source_url: str = ""
    # 数据源 API Key（部分收费/鉴权数据源需要，内置源无需填写）。
    # 若填写，会作为请求头 "Authorization: Bearer <key>" 发送。
    api_key: str = ""
    # 文字滚动速度（像素/帧），越大越快
    scroll_speed: int = 2
    # 滚动帧间隔（毫秒），越小越顺滑但更耗 CPU
    scroll_fps_ms: int = 30
    # 组件高度（像素）
    height: int = 30
    # 组件宽度（像素）
    width: int = 380
    # 字号
    font_size: int = 12
    # 字体
    font_family: str = "Microsoft YaHei"
    # 背景样式预设：控制悬浮窗背景与主文字配色，可在设置界面下拉选择。
    #   "dark"        深色底（默认）：深灰背景 + 浅色文字
    #   "pearl"       珍珠白底：柔和米白背景 + 深色文字
    #   "transparent" 完全透明：不画卡片背景，仅悬浮文字（叠在桌面上）
    bg_style: str = "dark"
    # 背景色（仅当 bg_style 为自定义/兼容旧配置时生效；预设样式会覆盖它）
    bg_color: str = "#1e1e1e"
    # 主文字颜色（名称/现价）。留空则跟随 bg_style 预设自动取色。
    fg_color: str = ""
    # 涨/跌/平的文字颜色（A 股习惯：红涨绿跌）
    up_color: str = "#ff4d4f"
    down_color: str = "#52c41a"
    flat_color: str = "#cccccc"
    # 是否尝试真正嵌入 Windows 任务栏（失败自动降级为贴底悬浮窗）
    embed_taskbar: bool = False
    # 非嵌入模式下窗口距屏幕右下角的偏移
    margin_right: int = 8
    margin_bottom: int = 40
    # 悬浮窗上次被拖动到的绝对屏幕坐标（物理像素）。
    #   -1 (默认) 表示"未设置"，此时按 margin_right/margin_bottom 贴屏幕右下角。
    #   >=0 时优先使用该坐标，实现拖动位置在重启后保持不变。
    # 拖动结束时会自动写回，"复原到默认位置"会把二者重置为 -1。
    pos_x: int = -1
    pos_y: int = -1
    # 全局快捷键：切换常驻悬浮行情条的显示/隐藏（方便摸鱼时快速藏起来）。
    # 格式为 "修饰键+按键"，修饰键支持 ctrl/alt/shift/win，按键支持
    # 字母 a-z、数字 0-9、F1-F12。例如 "ctrl+alt+s"。留空则不注册热键。
    hotkey: str = "ctrl+alt+s"
    # 程序启动时悬浮行情条是否默认可见。False 则启动后先隐藏，靠热键/菜单唤出。
    float_on_start: bool = True
    # 悬浮窗是否始终置顶（顶在所有窗口最前）。
    #   True  (默认) 每轮轮询重申 HWND_TOPMOST，切换其它窗口/全屏后仍保持在最前。
    #   False 只作为普通悬浮窗，会被其它窗口正常遮挡（不再强制抢占 Z 序最前）。
    always_on_top: bool = True
    # 是否随 Windows 登录开机自启（写入当前用户注册表 Run 键，无需管理员权限）。
    # 该值仅记录期望状态；实际的注册表登记/注销在设置保存时由 autostart 模块同步。
    auto_start: bool = False
    # 悬浮窗展示方式：
    #   "horizontal" 横向滚动跑马灯（所有股票拼成一行从右向左匀速滚动）
    #   "vertical"   上下切换轮播（每次只显示一只，停留数秒后向上滚动切到下一只）
    display_mode: str = "horizontal"
    # vertical 模式下每只股票的停留时长（毫秒），停留结束后播放切换动画
    vertical_dwell_ms: int = 3000
    # 悬浮窗圆角半径（像素）。>0 时用透明键抠出圆角卡片，0 则为直角纯色背景。
    # 若系统不支持窗口透明色会自动降级为直角。
    corner_radius: int = 12
    # transparent 背景样式下的窗口不透明度（0.0~1.0）。
    # 说明：色键透明（-transparentcolor）会让透明像素在 Windows 上被鼠标穿透，
    # 导致透明背景区无法拖拽。这里改用窗口级 alpha 半透明透出桌面，整窗可点可拖。
    # 值越小越透明，过小会看不清文字，建议 0.7~0.9。
    transparent_alpha: float = 0.85

    def palette(self) -> dict:
        """按 bg_style 返回悬浮窗配色方案。

        返回字段:
          * ``bg``           卡片/窗口背景色
          * ``fg``           主文字颜色（名称、现价）
          * ``muted``        次要文字颜色（占位提示等）
          * ``transparent``  是否整窗透明（不画卡片背景）

        设计:预设优先。选中 pearl/dark/transparent 时忽略手填 bg_color/fg_color，
        保证配色协调；只有 style 不在预设内时才回退到自定义 bg_color/fg_color。
        """
        presets = {
            "dark": {
                "bg": "#1e1e1e", "fg": "#f5f5f5",
                "muted": "#cccccc", "transparent": False,
            },
            "pearl": {
                "bg": "#f5f3ec", "fg": "#2b2b2b",
                "muted": "#6b6b6b", "transparent": False,
            },
            "transparent": {
                "bg": "#1e1e1e", "fg": "#f5f5f5",
                "muted": "#dddddd", "transparent": True,
            },
        }
        if self.bg_style in presets:
            return dict(presets[self.bg_style])
        # 自定义/兼容旧配置:用手填的 bg_color，fg_color 缺省时退回浅色
        return {
            "bg": self.bg_color or "#1e1e1e",
            "fg": self.fg_color or "#f5f5f5",
            "muted": self.flat_color or "#cccccc",
            "transparent": False,
        }

    @classmethod
    def load(cls, path: str = DEFAULT_CONFIG_NAME) -> "Config":
        if not os.path.exists(path):
            cfg = cls()
            cfg.save(path)
            logger.info("已生成默认配置: %s", os.path.abspath(path))
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("读取配置失败，使用默认值: %s", exc)
            return cls()
        # 只取已知字段，忽略多余键，缺失键用默认值
        known = {f for f in cls().__dict__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def save(self, path: str = DEFAULT_CONFIG_NAME) -> None:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("保存配置失败: %s", exc)
