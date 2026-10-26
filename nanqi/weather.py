r"""weather.py —— 当日天气（Open-Meteo，公开接口、**无需 API key**）。

【为什么要它】
用户的诉求是「天气不好就早一点提醒我出门」。所以这里只做三件事，而且刻意做窄：

  1. 查**今天**的天气（降水、最大风速、最低温、天气码）；
  2. 给一个「恶劣 / 正常」的判断与一句人话说明；
  3. 其它什么都不做——提醒节奏由 `course_reminder.py` 决定（提前 45 分钟 + 文案）。

【为什么用 Open-Meteo】
科大教务与天气这类小工具最怕「注册一个 key 然后过期」。Open-Meteo 免费、无鉴权、
返回 JSON，默认坐标是合肥（31.84N, 117.26E），可在设置里改。

【这是应用的第二个对外网络请求】
第一个是更新检查（`update_checker.py`，且默认关闭）。天气请求也只在
「天气提醒已开启 + 当天确实有课」时才发出，一天一次并缓存当日结果
（见 `controller._today_weather`）。README 的「已知差异」里有一条专门说明。

【失败必须静默降级】
网络不通、被墙、返回格式变了——这些都不该让提醒流程出问题。所以 `fetch_daily`
抛出的异常一律由调用方吞掉，最终退化成「没有天气信息」，即按常规 30 分钟提醒。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date

__all__ = [
    "DEFAULT_CITY",
    "DEFAULT_LATITUDE",
    "DEFAULT_LONGITUDE",
    "BAD_PRECIP_MM",
    "BAD_WIND_KMH",
    "BAD_TEMP_MIN_C",
    "normalize_settings",
    "judge",
    "build_url",
    "parse_daily",
    "fetch_daily",
    "weather_note",
]

API_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_CITY = "合肥"
DEFAULT_LATITUDE = 31.84
DEFAULT_LONGITUDE = 117.26

# 判据（都能用真实天气反驳自己，所以写在这里当「可调参数」而不是散在判断里）：
BAD_PRECIP_MM = 1.0      # 日累计降水 >= 1 mm 就算有雨（毛毛雨不打扰）
BAD_WIND_KMH = 30.0      # 日最大风速 >= 30 km/h 算大风（骑车明显顶风）
BAD_TEMP_MIN_C = 0.0     # 最低温 <= 0℃ 算严寒（路面可能结冰）

# 实测（本机，合肥→api.open-meteo.com）握手就要 5–6 秒，所以超时给到 12 秒：
# 这个请求跑在后台线程里（见 `controller._today_weather`），等久一点不会卡界面，
# 但超时太短会白白丢掉一次当天唯一的查询机会。
REQUEST_TIMEOUT_S = 12.0
MAX_NOTE_ITEMS = 2

# WMO 天气码 → 中文（只列会出现「恶劣」判断的那几种，其余不显示）。
_WMO_TEXT = {
    45: "有雾",
    48: "有雾凇",
    51: "有毛毛雨",
    53: "有毛毛雨",
    55: "有毛毛雨",
    56: "有冻雨",
    57: "有冻雨",
    61: "有雨",
    63: "有中雨",
    65: "有大雨",
    66: "有冻雨",
    67: "有冻雨",
    71: "有雪",
    73: "有中雪",
    75: "有大雪",
    77: "有雪粒",
    80: "有阵雨",
    81: "有阵雨",
    82: "有强阵雨",
    85: "有阵雪",
    86: "有强阵雪",
    95: "有雷雨",
    96: "有雷雨伴冰雹",
    99: "有雷雨伴冰雹",
}


def _number(raw, fallback: float) -> float:
    """把设置里的数值（可能是字符串、None、布尔）收敛成有限浮点。"""
    if isinstance(raw, bool):
        return fallback
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return fallback
    return value


def normalize_settings(raw) -> dict:
    """天气设置的白名单与边界（写进 `plans.json` 的 settings.weather）。

    默认**关闭**：这是一个会联网的功能，和「自动检查更新」一样按 opt-in 处理。
    """
    source = raw if isinstance(raw, dict) else {}
    latitude = _number(source.get("latitude"), DEFAULT_LATITUDE)
    longitude = _number(source.get("longitude"), DEFAULT_LONGITUDE)
    city = source.get("city")
    if not isinstance(city, str) or not city.strip():
        city = DEFAULT_CITY
    return {
        "enabled": source.get("enabled") is True,
        "latitude": round(min(90.0, max(-90.0, latitude)), 4),
        "longitude": round(min(180.0, max(-180.0, longitude)), 4),
        "city": city.strip()[:20],
    }


def build_url(latitude: float, longitude: float, day: date | str) -> str:
    """当日预报的 URL（只取判断所需的四个量，`timezone=auto` 让日期按当地算）。"""
    day_text = day.isoformat() if isinstance(day, date) else str(day)
    query = urllib.parse.urlencode(
        {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "daily": "precipitation_sum,wind_speed_10m_max,temperature_2m_min,weather_code",
            "timezone": "auto",
            "start_date": day_text,
            "end_date": day_text,
        }
    )
    return f"{API_URL}?{query}"


def parse_daily(payload, day: date | str) -> dict:
    """从 Open-Meteo 的响应里取当日四个量；结构不对时抛 ValueError。

    返回：`{precipitation_mm, wind_kmh, temp_min_c, code}`（缺失的量为 0/None）。
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("天气接口返回格式不正确")
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("天气接口没有返回 daily 数据")
    day_text = day.isoformat() if isinstance(day, date) else str(day)
    times = daily.get("time")
    index = 0
    if isinstance(times, list) and times:
        if day_text in times:
            index = times.index(day_text)
        else:
            index = 0

    def pick(key: str):
        values = daily.get(key)
        if not isinstance(values, list) or index >= len(values):
            return None
        return values[index]

    code = pick("weather_code")
    return {
        "date": day_text,
        "precipitation_mm": _number(pick("precipitation_sum"), 0.0),
        "wind_kmh": _number(pick("wind_speed_10m_max"), 0.0),
        "temp_min_c": _number(pick("temperature_2m_min"), 0.0),
        "code": int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else None,
    }


def judge(day: dict) -> dict:
    """判断这一天算不算「恶劣」，并给出人话说明。

    返回 `{bad, reasons, text}`；`text` 最多两条原因（提示语要短，托盘气泡塞不下长句）。
    """
    reasons: list[str] = []
    if not isinstance(day, dict):
        return {"bad": False, "reasons": [], "text": ""}
    precipitation = _number(day.get("precipitation_mm"), 0.0)
    wind = _number(day.get("wind_kmh"), 0.0)
    temperature = day.get("temp_min_c")
    temperature = None if temperature is None else _number(temperature, 0.0)

    code = day.get("code")
    code_text = _WMO_TEXT.get(code) if isinstance(code, int) else None
    if precipitation >= BAD_PRECIP_MM:
        reasons.append(code_text or "有雨")
    elif code_text and code_text in ("有雷雨", "有雷雨伴冰雹", "有雪", "有中雪", "有大雪", "有冻雨", "有雾"):
        reasons.append(code_text)
    if wind >= BAD_WIND_KMH:
        reasons.append(f"大风 {wind:.0f} km/h")
    if temperature is not None and temperature <= BAD_TEMP_MIN_C:
        reasons.append(f"最低 {temperature:.0f}℃")

    kept = reasons[:MAX_NOTE_ITEMS]
    return {"bad": bool(reasons), "reasons": reasons, "text": " · ".join(kept)}


def fetch_daily(
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    day: date | str | None = None,
    *,
    fetch_impl=None,
    timeout: float = REQUEST_TIMEOUT_S,
) -> dict:
    """查询某天（默认今天）的天气并附上判断结果。

    `fetch_impl(url, timeout) -> bytes|str` 可注入，测试里用它避免真的联网。
    网络或格式错误会以异常抛出，由调用方降级处理。
    """
    target = day or date.today()
    url = build_url(latitude, longitude, target)
    text = (fetch_impl or _http_get)(url, timeout)
    day_data = parse_daily(text, target)
    day_data["judge"] = judge(day_data)
    return day_data


def _http_get(url: str, timeout: float = REQUEST_TIMEOUT_S) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "nanqi_schedule/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定 https 主机
        return response.read().decode("utf-8", "replace")


def weather_note(day: dict | None) -> str:
    """把判断结果变成提示语；不恶劣或没有数据时返回空字符串。"""
    if not isinstance(day, dict):
        return ""
    result = day.get("judge") if isinstance(day.get("judge"), dict) else judge(day)
    if not result.get("bad"):
        return ""
    from .course_reminder import BAD_WEATHER_NOTE

    return f"{result.get('text', '')}，{BAD_WEATHER_NOTE}"
