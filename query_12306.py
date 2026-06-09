"""
12306 车票查询模块 — 纯 requests 实现，无需浏览器。
功能：按始发/终到城市查车次、同城车站、发到时间、实时余票。
"""

import json
import os
import random
import re
import sys
import time
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
STATION_URL = (
    "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
)
INIT_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
QUERY_URL = "https://kyfw.12306.cn/otn/leftTicket/query?"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

# 缓存目录：优先 exe 同目录（含 PyInstaller），其次脚本目录
if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_BASE, ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "stations.json")
_CACHE_TTL = 86400 * 7  # 车站列表缓存 7 天

# 反爬虫：每次发请求前的随机休眠秒数范围
_DELAY_MIN = 0.5
_DELAY_MAX = 2.5

# 持久化 session，维持 cookie
_SESSION: requests.Session | None = None


def _random_delay() -> None:
    """随机休眠，降低触发 12306 反爬虫的概率。"""
    delay = random.uniform(_DELAY_MIN, _DELAY_MAX)
    time.sleep(delay)


def _get_session() -> requests.Session:
    """获取或创建带 cookie 的 requests Session。"""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({"User-Agent": USER_AGENT})
        _SESSION.verify = False
    return _SESSION


def _ensure_cookies() -> None:
    """确保 session 持有有效的 12306 cookie。"""
    s = _get_session()
    if s.cookies.get("JSESSIONID") and s.cookies.get("route"):
        return
    _random_delay()
    s.get(INIT_URL, timeout=15)

# ---------------------------------------------------------------------------
# 车站数据
# ---------------------------------------------------------------------------

def _fetch_station_map() -> dict[str, dict[str, str]]:
    """从 12306 拉取车站名 → {code, telecode, pinyin} 映射。"""
    s = _get_session()
    _random_delay()
    resp = s.get(STATION_URL, timeout=15)
    resp.encoding = "utf-8"
    text = resp.text

    # 格式: @pinyin|站名|电报码|拼音|拼音首字母|序号 —— 这个格式会变，用宽松正则
    stations: dict[str, dict[str, str]] = {}
    for m in re.finditer(
        r"@([a-z]+)\|([^|]+)\|([A-Z]{3})\|([a-z]+)\|([a-z]+)\|\d+", text
    ):
        pinyin, name, telecode, *_ = m.groups()
        stations[name] = {"telecode": telecode, "pinyin": pinyin}
    return stations


def get_station_map(force_refresh: bool = False) -> dict[str, dict[str, str]]:
    """返回车站映射，默认使用本地缓存。"""
    if not force_refresh and os.path.exists(_CACHE_FILE):
        try:
            cached_at = os.path.getmtime(_CACHE_FILE)
            if time.time() - cached_at < _CACHE_TTL:
                with open(_CACHE_FILE, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    stations = _fetch_station_map()
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(stations, f, ensure_ascii=False, indent=2)
    return stations


def find_stations(city_name: str) -> dict[str, dict[str, str]]:
    """按城市名查找同城所有车站。

    Args:
        city_name: 城市名，如 ``"北京"``、``"上海"``、``"武汉"``。

    Returns:
        {站名: {telecode, pinyin}} 的字典。

    Example:
        >>> find_stations("北京")
        {'北京': {'telecode': 'BJP', 'pinyin': 'beijing'},
         '北京北': {'telecode': 'VAP', 'pinyin': 'beijingbei'},
         '北京南': {'telecode': 'VNP', 'pinyin': 'beijingnan'},
         ...}
    """
    all_stations = get_station_map()
    result: dict[str, dict[str, str]] = {}

    # 精确匹配 + 前缀匹配（收录以城市名 开头的站，如 "北京西"、"北京南"）
    for name, info in all_stations.items():
        if name == city_name or name.startswith(city_name):
            result[name] = info
    return result


# ---------------------------------------------------------------------------
# 余票查询
# ---------------------------------------------------------------------------

# 12306 返回的 result 数组里每条记录是 | 分隔的字符串，字段顺序固定。
# data.map 在新版 API 中是 站名映射（电报码→中文），不是席位映射。
#
# 席位余票在 result 数组中的位置因列车类型而异，通过 train_seat_feature
# （第 14 个字段）和车次前缀来判定。

# 席位中文名
_SEAT_CN: dict[str, str] = {
    "SW": "商务座",
    "TZ": "特等座",
    "Y1": "优选一等座",
    "ZY": "一等座",
    "ZE": "二等座",
    "WY": "动卧/一等卧",
    "WE": "二等卧",
    "GR": "高级软卧",
    "WR": "软卧",
    "YW": "硬卧",
    "RZ": "软座",
    "YZ": "硬座",
    "WZ": "无座"
}

# 席位展示固定顺序（左→右），与 12306 返回数据的排列一致
# 不在列表中的席位追加到末尾
SEAT_DISPLAY_ORDER: list[str] = [
    "SW", "TZ", "Y1", "ZY", "ZE", "WY",
    "WE", "GR", "WR", "YW", "RZ", "YZ",
    "WZ"
]

# 12306 返回坐席解析
# |20|21|22|23   |24|25|26|27|28   |29|30|31|32|33|
# |Y1|GR|22|WY/WR|24|TZ|WZ|27|WE/YW|YZ|ZE|ZY|SW|33|

# 席位 → result 数组偏移，按列车类型前缀区分
# 无座 WZ 总是在 [26]，其他席位位置各异
_SEAT_OFFSETS: dict[str, dict[int, str]] = {
    # G 高铁：26=WZ, 30=ZE, 31=ZY, 32=SW
    "G": {20: "Y1", 23: "WY", 25: "TZ", 30: "ZE", 31: "ZY", 32: "SW"},
    # C 城际：同 G
    "C": {20: "Y1", 23: "WY", 25: "TZ", 30: "ZE", 31: "ZY", 32: "SW"},
    # D 动车：26=WZ, 28=ZY, 29=ZE, 30=SRRB(动卧)
    "D": {20: "Y1", 23: "WY", 25: "TZ", 28: "WE", 30: "ZE", 31: "ZY", 32: "SW"},
    # K/T/Z/纯数字 普速：26=WZ, 28=YZ, 29=RZ, 30=YW, 31=RW, 32=GR
    "_default": {21: "GR", 23: "WR", 26: "WZ", 28: "YW", 29: "YZ"},
}


def _get_seat_offsets(train_code: str, train_seat_feature: str) -> dict[int, str]:
    """根据车次和特征码返回 {result偏移: 席位代码}。"""
    prefix = train_code[0] if train_code and train_code[0].isalpha() else "_default"
    if prefix in _SEAT_OFFSETS:
        return _SEAT_OFFSETS[prefix]

    # G 系列有时特征码不同（如 feature=4 表示有特等座），覆盖
    if prefix == "G" and train_seat_feature == "4":
        return {26: "WZ", 30: "TZ", 31: "ZY", 32: "ZE"}
    return _SEAT_OFFSETS["_default"]


def _clean(val: str) -> str:
    """清洗单个字段值。"""
    v = val.strip()
    if not v:
        return "--"
    return v


def _parse_ticket_result(
    result_str: str,
    station_names: dict[str, str],
) -> dict[str, Any] | None:
    """解析单条 | 分隔的余票数据。"""
    parts = result_str.split("|")
    if len(parts) < 58:
        return None

    train_code = _clean(parts[3])
    if not train_code or train_code == "--":
        return None

    seat_feature = _clean(parts[14])
    seat_offsets = _get_seat_offsets(train_code, seat_feature)

    # 将电报码转为中文站名
    def _sname(telecode: str) -> str:
        return station_names.get(telecode, telecode)

    seats: dict[str, str] = {}
    for idx, seat_code in seat_offsets.items():
        seats[seat_code] = _clean(parts[idx]) if idx < len(parts) else "--"

    return {
        "train_code": train_code,
        "start_station": _sname(_clean(parts[4])),
        "end_station": _sname(_clean(parts[5])),
        "from_station": _sname(_clean(parts[6])),
        "to_station": _sname(_clean(parts[7])),
        "depart_time": _clean(parts[8]),
        "arrive_time": _clean(parts[9]),
        "duration": _clean(parts[10]),
        "can_buy": _clean(parts[11]) == "Y",
        "start_date": _clean(parts[13]),
        "from_station_no": _clean(parts[16]),
        "to_station_no": _clean(parts[17]),
        "seats": seats,
    }


def _resolve_station(city_or_station: str) -> tuple[str, str]:
    """将城市名或站名解析为 (站名, 电报码)。"""
    station_map = get_station_map()

    # 精确匹配站名
    if city_or_station in station_map:
        info = station_map[city_or_station]
        return city_or_station, info["telecode"]

    # 按城市名找第一个匹配站（作为默认站）
    candidates = find_stations(city_or_station)
    if not candidates:
        raise ValueError(
            f"未找到车站或城市: {city_or_station}"
        )
    # 优先精确匹配，即站名 == 城市名（如 "北京" 既是市也是站）
    if city_or_station in candidates:
        return city_or_station, candidates[city_or_station]["telecode"]
    # 否则取第一个
    name, info = next(iter(candidates.items()))
    return name, info["telecode"]


def query_tickets(
    date: str,
    from_city: str,
    to_city: str,
    purpose: str = "ADULT",
) -> dict[str, Any]:
    """查询 12306 火车票。

    Args:
        date: 乘车日期，格式 ``YYYY-MM-DD``。
        from_city: 始发城市或站名，如 ``"北京"``、``"上海虹桥"``。
        to_city: 终到城市或站名。
        purpose: 乘客类型，默认 ``ADULT``（成人）。

    Returns:
        {
            "date": str,
            "from_city": str,
            "to_city": str,
            "from_stations": {站名: info},    # 始发同城所有车站
            "to_stations": {站名: info},      # 终到同城所有车站
            "seat_types": {code: 中文名},     # 本次可用的席位类型
            "trains": [                        # 列车列表
                {
                    "train_code": str,         # 车次
                    "start_station": str,      # 始发站
                    "end_station": str,        # 终到站
                    "from_station": str,       # 上车站
                    "to_station": str,         # 下车站
                    "depart_time": str,        # 发车时间
                    "arrive_time": str,        # 到站时间
                    "duration": str,           # 历时
                    "can_buy": bool,
                    "seats": {code: str},      # 各席位余票
                },
                ...
            ],
            "raw_result": list[str],           # 原始返回数据
        }

    Raises:
        ValueError: 车站/城市名无法解析时。
        requests.RequestException: 网络请求失败时。

    Example:
        >>> result = query_tickets("2026-05-20", "北京", "上海")
        >>> for t in result["trains"]:
        ...     print(t["train_code"], t["depart_time"], t["arrive_time"])
    """
    station_map = get_station_map()

    from_name, from_code = _resolve_station(from_city)
    to_name, to_code = _resolve_station(to_city)

    # 同城车站
    from_stations = find_stations(from_city)
    to_stations = find_stations(to_city)

    params = {
        "leftTicketDTO.train_date": date,
        "leftTicketDTO.from_station": from_code,
        "leftTicketDTO.to_station": to_code,
        "purpose_codes": purpose,
    }

    s = _get_session()
    _ensure_cookies()
    _random_delay()
    s.headers["Referer"] = "https://kyfw.12306.cn/otn/leftTicket/init"
    resp = s.get(QUERY_URL, params=params, timeout=15)
    # 12306 偶尔返回带 BOM 的响应，用 utf-8-sig 解码
    data = json.loads(resp.content.decode("utf-8-sig"))

    if not data.get("status") and not data.get("httpstatus") == 200:
        return {
            "date": date,
            "from_city": from_city,
            "to_city": to_city,
            "from_stations": from_stations,
            "to_stations": to_stations,
            "error": data.get("messages", "查询失败"),
            "trains": [],
        }

    raw_result = data.get("data", {}).get("result", [])
    station_names: dict[str, str] = data.get("data", {}).get("map", {})

    trains: list[dict[str, Any]] = []
    seen_seat_codes: set[str] = set()
    for line in raw_result:
        t = _parse_ticket_result(line, station_names)
        if t:
            trains.append(t)
            seen_seat_codes.update(t["seats"].keys())

    # 只展示本次查询中出现的席位类型
    seat_cn: dict[str, str] = {}
    for code in seen_seat_codes:
        seat_cn[code] = _SEAT_CN.get(code, code)

    return {
        "date": date,
        "from_city": from_city,
        "to_city": to_city,
        "from_stations": from_stations,
        "to_stations": to_stations,
        "station_names": station_names,
        "seat_types": seat_cn,
        "trains": trains,
        "raw_result": raw_result,
        "_raw_response": data,
    }


