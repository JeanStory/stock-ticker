"""行情数据获取层。

主数据源为腾讯财经 (qt.gtimg.cn)，失败时自动回退到新浪财经 (hq.sinajs.cn)。
两个接口都返回逗号/分号分隔的文本，无需 API Key，适合轻量级实时刷新。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

# 请求超时（秒）。行情接口通常很快，超时设短一点避免 UI 卡顿。
_TIMEOUT = 6

# 伪装成浏览器，部分接口对空 UA 或异常 UA 会拒绝。
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    # 新浪接口会校验 Referer，缺失会返回 403。
    "Referer": "https://finance.sina.com.cn",
}


@dataclass
class Quote:
    """单只标的的行情快照。"""

    code: str          # 原始代码，如 "sh600519"
    name: str          # 名称，如 "贵州茅台"
    price: float       # 现价
    prev_close: float  # 昨收
    change: float      # 涨跌额
    change_pct: float  # 涨跌幅（百分比，例如 0.55 表示 +0.55%）

    @property
    def is_up(self) -> bool:
        return self.change_pct > 0

    @property
    def is_down(self) -> bool:
        return self.change_pct < 0


def normalize_code(code: str) -> str:
    """把用户输入的股票代码规范成接口需要的带市场前缀格式。

    支持的输入::

        "600519"      -> "sh600519"   （6/9 开头视为沪市）
        "000001"      -> "sz000001"   （0/2/3 开头视为深市）
        "sh600519"    -> "sh600519"   （已带前缀原样返回）
        "00700.HK"    -> "hk00700"    （港股，补零到 5 位）
        "hk00700"     -> "hk00700"
    """
    raw = code.strip().lower()
    if not raw:
        raise ValueError("股票代码不能为空")

    # 已经带市场前缀
    if raw.startswith(("sh", "sz", "hk", "us")):
        return raw

    # 港股: "00700.hk" / "700.hk"
    if raw.endswith(".hk"):
        num = raw[:-3]
        return "hk" + num.zfill(5)

    # 纯数字 A 股，按首位判断沪深
    if raw.isdigit():
        if raw.startswith(("6", "9")):
            return "sh" + raw
        if raw.startswith(("0", "2", "3")):
            return "sz" + raw
        # 兜底当作沪市
        return "sh" + raw

    # 其它情况（如美股代码）默认加 us 前缀
    return "us" + raw


def _parse_tencent(payload: str) -> list[Quote]:
    """解析腾讯接口返回文本。

    每行形如::

        v_sh600519="1~贵州茅台~600519~1192.04~1185.49~...~0.55~...";
    """
    quotes: list[Quote] = []
    for line in payload.strip().splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        var, _, rest = line.partition("=")
        content = rest.strip().strip(";").strip('"')
        fields = content.split("~")
        if len(fields) < 33:
            # 无效代码时腾讯会返回空内容 v_xxx="";
            logger.warning("跳过无效行情: %s", var)
            continue
        # 从变量名恢复原始代码，如 v_sh600519 -> sh600519。
        # 港股实时源用 r_hk 前缀请求，这里还原成 hk 以匹配用户配置。
        code = var.replace("v_", "", 1)
        if code.startswith("r_hk"):
            code = code[2:]
        try:
            quotes.append(
                Quote(
                    code=code,
                    name=fields[1],
                    price=float(fields[3]),
                    prev_close=float(fields[4]),
                    change=float(fields[31]),
                    change_pct=float(fields[32]),
                )
            )
        except (ValueError, IndexError) as exc:
            logger.warning("解析行情失败 %s: %s", code, exc)
    return quotes


def _parse_sina(payload: str) -> list[Quote]:
    """解析新浪接口返回文本（回退数据源）。

    每行形如::

        var hq_str_sh600519="贵州茅台,1180.10,1185.49,1192.04,...";

    字段: [0]名称 [1]今开 [2]昨收 [3]现价 ...
    """
    quotes: list[Quote] = []
    for line in payload.strip().splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        var, _, rest = line.partition("=")
        content = rest.strip().strip(";").strip('"')
        fields = content.split(",")
        if len(fields) < 4 or not fields[0]:
            continue
        code = var.split("hq_str_")[-1].strip()
        try:
            price = float(fields[3])
            prev_close = float(fields[2])
            change = round(price - prev_close, 3)
            change_pct = round(change / prev_close * 100, 2) if prev_close else 0.0
            quotes.append(
                Quote(
                    code=code,
                    name=fields[0],
                    price=price,
                    prev_close=prev_close,
                    change=change,
                    change_pct=change_pct,
                )
            )
        except (ValueError, IndexError) as exc:
            logger.warning("解析新浪行情失败 %s: %s", code, exc)
    return quotes


# 内置数据源的默认地址。source_url 留空时使用这里的默认值。
_TENCENT_URL = "http://qt.gtimg.cn/q="
_SINA_URL = "http://hq.sinajs.cn/list="


def _build_headers(api_key: str = "") -> dict:
    headers = dict(_HEADERS)
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def _to_tencent_realtime(code: str) -> str:
    """把港股代码转成腾讯的实时前缀。

    腾讯免费接口对 ``hk`` 前缀默认返回延迟约 15 分钟的行情，
    带 ``r_`` 前缀（如 ``r_hk00700``）才是实时行情。字段结构与
    延迟源完全一致，因此只需在请求时替换前缀，解析层无需改动。
    A 股 / 美股不受影响，原样返回。
    """
    if code.startswith("hk"):
        return "r_" + code
    return code


def _fetch_tencent(
    codes: list[str], base_url: str = "", api_key: str = ""
) -> list[Quote]:
    # 港股自动切换到实时前缀 r_hk，对用户输入与界面显示透明。
    req_codes = [_to_tencent_realtime(c) for c in codes]
    url = (base_url or _TENCENT_URL) + ",".join(req_codes)
    resp = requests.get(url, headers=_build_headers(api_key), timeout=_TIMEOUT)
    resp.encoding = "gbk"
    resp.raise_for_status()
    return _parse_tencent(resp.text)


def _fetch_sina(
    codes: list[str], base_url: str = "", api_key: str = ""
) -> list[Quote]:
    url = (base_url or _SINA_URL) + ",".join(codes)
    resp = requests.get(url, headers=_build_headers(api_key), timeout=_TIMEOUT)
    resp.encoding = "gbk"
    resp.raise_for_status()
    return _parse_sina(resp.text)


def _fetch_custom(codes: list[str], base_url: str, api_key: str = "") -> list[Quote]:
    """自定义数据源：以 base_url + 逗号分隔代码 发起 GET，
    自动尝试腾讯 / 新浪两种文本格式解析（谁能解析出结果用谁）。
    """
    if not base_url:
        raise ValueError("source_type=custom 但未配置 source_url")
    url = base_url + ",".join(codes)
    resp = requests.get(url, headers=_build_headers(api_key), timeout=_TIMEOUT)
    resp.encoding = "gbk"
    resp.raise_for_status()
    quotes = _parse_tencent(resp.text)
    if not quotes:
        quotes = _parse_sina(resp.text)
    return quotes


def fetch_quotes(
    codes: Iterable[str],
    source_type: str = "tencent",
    source_url: str = "",
    api_key: str = "",
) -> list[Quote]:
    """批量拉取行情，返回顺序与输入尽量一致。

    ``source_type`` 决定主数据源（tencent / sina / custom），失败时按
    腾讯→新浪的顺序回退（custom 源失败也回退到内置源，保证可用性）。
    ``source_url`` 覆盖数据源地址，``api_key`` 作为 Bearer 鉴权头发送。
    任何一个 code 解析失败都不会影响其它 code；全部失败返回空列表
    （调用方负责保留上一次的数据）。
    """
    normalized = [normalize_code(c) for c in codes]
    if not normalized:
        return []

    stype = (source_type or "tencent").strip().lower()

    # 按 source_type 组织主源 + 回退链（去重，保证顺序）
    def _primary():
        if stype == "sina":
            return _fetch_sina(normalized, source_url, api_key)
        if stype == "custom":
            return _fetch_custom(normalized, source_url, api_key)
        return _fetch_tencent(normalized, source_url, api_key)

    attempts = [("主数据源(%s)" % stype, _primary)]
    # 内置源之间互为回退；custom 也回退到内置默认源
    if stype != "tencent":
        attempts.append(("腾讯(回退)", lambda: _fetch_tencent(normalized)))
    if stype != "sina":
        attempts.append(("新浪(回退)", lambda: _fetch_sina(normalized)))

    quotes: list[Quote] = []
    for name, fn in attempts:
        try:
            quotes = fn()
        except Exception as exc:  # noqa: BLE001 - 网络异常统一回退
            logger.warning("%s 获取失败: %s", name, exc)
            continue
        if quotes:
            break

    if not quotes:
        logger.error("所有数据源均未取到行情")
        return []

    # 按输入顺序排序，找不到的排到最后
    order = {code: i for i, code in enumerate(normalized)}
    quotes.sort(key=lambda q: order.get(q.code, len(order)))
    return quotes
