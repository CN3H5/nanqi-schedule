"""test_weather.py —— 当日天气（Open-Meteo）的解析、判据与降级。

覆盖三件事：
  * `parse_daily` 能按日期取出四个量（不依赖真实网络）；
  * `judge` 的「恶劣」判据与提示语（雨 / 大风 / 低温）；
  * `normalize_settings` 的默认值与边界（老备份没有这个键 → 关闭、坐标合肥）。

联网一律用注入的 `fetch_impl` 替身，测试**不访问网络**。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from nanqi.weather import (
    BAD_PRECIP_MM,
    BAD_TEMP_MIN_C,
    BAD_WIND_KMH,
    DEFAULT_CITY,
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    build_url,
    fetch_daily,
    judge,
    normalize_settings,
    parse_daily,
    weather_note,
)

DAY = date(2026, 10, 21)


def payload(**overrides):
    daily = {
        "time": ["2026-10-21"],
        "precipitation_sum": [0.0],
        "wind_speed_10m_max": [8.0],
        "temperature_2m_min": [12.0],
        "weather_code": [1],
    }
    daily.update(overrides)
    return {"daily": daily}


# ------------------------------------------------------------------ 设置


def test_settings_default_off_with_hefei_coordinates():
    settings = normalize_settings(None)
    assert settings == {
        "enabled": False,
        "latitude": DEFAULT_LATITUDE,
        "longitude": DEFAULT_LONGITUDE,
        "city": DEFAULT_CITY,
    }


def test_settings_only_accepts_whitelisted_keys_and_clamps():
    settings = normalize_settings(
        {"enabled": True, "latitude": 999, "longitude": -999, "city": "  上海  ", "evil": 1}
    )
    assert settings["enabled"] is True
    assert settings["latitude"] == 90.0
    assert settings["longitude"] == -180.0
    assert settings["city"] == "上海"
    assert "evil" not in settings


def test_settings_reject_non_numeric_and_boolean_coordinates():
    settings = normalize_settings({"latitude": "abc", "longitude": True})
    assert settings["latitude"] == DEFAULT_LATITUDE
    assert settings["longitude"] == DEFAULT_LONGITUDE
    assert normalize_settings({"enabled": "yes"})["enabled"] is False


# ------------------------------------------------------------------ URL 与解析


def test_build_url_targets_single_day_with_timezone_auto():
    url = build_url(31.84, 117.26, DAY)
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    assert "start_date=2026-10-21" in url and "end_date=2026-10-21" in url
    assert "timezone=auto" in url
    assert "precipitation_sum" in url


def test_parse_daily_reads_four_values():
    day = parse_daily(json.dumps(payload(), ensure_ascii=False), DAY)
    assert day["date"] == "2026-10-21"
    assert day["precipitation_mm"] == 0.0
    assert day["wind_kmh"] == 8.0
    assert day["temp_min_c"] == 12.0
    assert day["code"] == 1


def test_parse_daily_picks_the_right_index_when_range_has_many_days():
    multi = payload(
        time=["2026-10-20", "2026-10-21"],
        precipitation_sum=[9.9, 0.0],
        wind_speed_10m_max=[99.0, 7.0],
        temperature_2m_min=[-9.0, 13.0],
        weather_code=[61, 1],
    )
    day = parse_daily(multi, DAY)
    assert day["precipitation_mm"] == 0.0
    assert day["wind_kmh"] == 7.0
    assert day["temp_min_c"] == 13.0


def test_parse_daily_rejects_broken_payloads():
    with pytest.raises(ValueError):
        parse_daily("not json", DAY)
    with pytest.raises(ValueError):
        parse_daily({"daily": None}, DAY)
    with pytest.raises(ValueError):
        parse_daily([], DAY)


# ------------------------------------------------------------------ 判据


def test_judge_good_weather():
    result = judge({"precipitation_mm": 0.0, "wind_kmh": 10.0, "temp_min_c": 15.0, "code": 1})
    assert result == {"bad": False, "reasons": [], "text": ""}


def test_judge_rain_uses_weather_code_text():
    result = judge(
        {"precipitation_mm": BAD_PRECIP_MM, "wind_kmh": 5.0, "temp_min_c": 18.0, "code": 61}
    )
    assert result["bad"] is True
    assert result["text"] == "有雨"


def test_judge_wind_and_cold_at_thresholds():
    assert judge({"wind_kmh": BAD_WIND_KMH})["bad"] is True
    assert judge({"wind_kmh": BAD_WIND_KMH - 0.1})["bad"] is False
    assert judge({"temp_min_c": BAD_TEMP_MIN_C})["bad"] is True
    assert judge({"temp_min_c": BAD_TEMP_MIN_C + 0.1})["bad"] is False


def test_judge_combines_reasons_but_keeps_text_short():
    result = judge(
        {"precipitation_mm": 12.0, "wind_kmh": 45.0, "temp_min_c": -3.0, "code": 65}
    )
    assert result["bad"] is True
    assert len(result["reasons"]) == 3
    assert len(result["text"].split(" · ")) == 2  # 托盘气泡塞不下长句


def test_judge_snow_and_thunder_without_precipitation_value():
    assert judge({"code": 71})["bad"] is True                  # 有雪
    assert judge({"code": 95})["bad"] is True                  # 有雷雨
    assert judge({"code": 2})["bad"] is False                  # 多云：不算恶劣
    assert judge(None)["bad"] is False


# ------------------------------------------------------------------ 取数与提示语


def test_fetch_daily_uses_injected_fetch_and_attaches_judgement():
    calls = []

    def fake(url, timeout):
        calls.append((url, timeout))
        return payload(precipitation_sum=[4.2], weather_code=[63])

    day = fetch_daily(31.84, 117.26, DAY, fetch_impl=fake)
    assert calls and calls[0][0].startswith("https://api.open-meteo.com")
    assert day["precipitation_mm"] == 4.2
    assert day["judge"]["bad"] is True


def test_weather_note_is_empty_for_good_weather():
    assert weather_note(None) == ""
    assert weather_note({"precipitation_mm": 0.0, "judge": {"bad": False}}) == ""
    assert weather_note({"precipitation_mm": 0.0, "wind_kmh": 0.0, "temp_min_c": 20.0}) == ""


def test_weather_note_mentions_reason_and_advice():
    note = weather_note({"precipitation_mm": 4.0, "code": 61})
    assert note.startswith("有雨")
    assert "今天天气不好，要早点出发" in note
