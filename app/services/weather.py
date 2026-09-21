"""Current weather for the dashboard card, from Open-Meteo (free, no API key): https://open-meteo.com/

One call gives the temperature, humidity and weather code now plus today's high and low. Answers are cached for
``WEATHER_CACHE_TTL`` (30 minutes) under the coordinates rounded to two decimals (about 1 km), so a student
refreshing the dashboard does not hit the service, and two students in the same suburb share one call.

Open-Meteo asks for attribution when its data is shown; the dashboard card carries it.
"""

from __future__ import annotations

import math

import requests
from flask import current_app

from app.extensions import cache
from app.services.http import ExternalAPIError, request_with_retry

SERVICE = "open-meteo"

# WMO weather interpretation codes -> (words, Bootstrap icon). https://open-meteo.com/en/docs
_CODES = {
    0: ("Clear sky", "sun"),
    1: ("Mainly clear", "sun"),
    2: ("Partly cloudy", "cloud-sun"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "cloud-fog"),
    48: ("Freezing fog", "cloud-fog"),
    51: ("Light drizzle", "cloud-drizzle"),
    53: ("Drizzle", "cloud-drizzle"),
    55: ("Heavy drizzle", "cloud-drizzle"),
    56: ("Freezing drizzle", "cloud-drizzle"),
    57: ("Freezing drizzle", "cloud-drizzle"),
    61: ("Light rain", "cloud-rain"),
    63: ("Rain", "cloud-rain"),
    65: ("Heavy rain", "cloud-rain-heavy"),
    66: ("Freezing rain", "cloud-rain"),
    67: ("Freezing rain", "cloud-rain-heavy"),
    71: ("Light snow", "cloud-snow"),
    73: ("Snow", "cloud-snow"),
    75: ("Heavy snow", "cloud-snow"),
    77: ("Snow grains", "cloud-snow"),
    80: ("Light showers", "cloud-rain"),
    81: ("Showers", "cloud-rain"),
    82: ("Heavy showers", "cloud-rain-heavy"),
    85: ("Snow showers", "cloud-snow"),
    86: ("Heavy snow showers", "cloud-snow"),
    95: ("Thunderstorm", "cloud-lightning-rain"),
    96: ("Thunderstorm with hail", "cloud-lightning-rain"),
    99: ("Thunderstorm with hail", "cloud-lightning-rain"),
}


class WeatherError(RuntimeError):
    """The weather service could not be reached or gave an unusable answer."""


def describe(code, is_day: bool = True) -> tuple[str, str]:
    """``(words, bootstrap icon)`` for a WMO weather code; an unknown code is called "Unknown"."""
    words, icon = _CODES.get(code, ("Unknown", "cloud"))
    if code in (0, 1) and not is_day:
        icon = "moon-stars"
    elif code == 2 and not is_day:
        icon = "cloud-moon"
    return words, icon


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WeatherError(f"Open-Meteo sent no usable {name}")
    return float(value)


def parse(data: dict) -> dict:
    """Turn Open-Meteo's answer into the small dict the dashboard shows. Raises :class:`WeatherError` if it is incomplete."""
    try:
        current, daily = data["current"], data["daily"]
        code = int(_number(current["weather_code"], "weather code"))
        is_day = bool(current.get("is_day", 1))
        words, icon = describe(code, is_day)
        return {
            "temperature_c": round(_number(current["temperature_2m"], "temperature")),
            "humidity": round(_number(current["relative_humidity_2m"], "humidity")),
            "condition": words,
            "icon": icon,
            "code": code,
            "is_day": is_day,
            "high_c": round(_number(daily["temperature_2m_max"][0], "high")),
            "low_c": round(_number(daily["temperature_2m_min"][0], "low")),
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise WeatherError("Open-Meteo's answer was not in the expected shape") from exc


def _fetch(lat: float, lng: float) -> dict:
    cfg = current_app.config
    try:
        response = request_with_retry(
            SERVICE,
            "GET",
            f"{cfg['OPEN_METEO_BASE_URL'].rstrip('/')}/forecast",
            retries=2,
            timeout=cfg["WEATHER_TIMEOUT"],
            params={
                "latitude": f"{lat:.4f}",
                "longitude": f"{lng:.4f}",
                "timezone": "Africa/Johannesburg",
                "forecast_days": 1,
                "current": "temperature_2m,relative_humidity_2m,weather_code,is_day",
                "daily": "temperature_2m_max,temperature_2m_min",
            },
        )
    except (ExternalAPIError, requests.RequestException) as exc:
        raise WeatherError("The weather service could not be reached") from exc
    if response.status_code != 200:
        raise WeatherError(f"The weather service answered HTTP {response.status_code}")
    try:
        return parse(response.json())
    except ValueError as exc:
        raise WeatherError("The weather service sent something that is not JSON") from exc


def get_weather(lat: float, lng: float) -> dict:
    """Weather at a point (cached 30 minutes per ~1 km cell). Raises :class:`WeatherError`."""
    key = f"weather:{lat:.2f}:{lng:.2f}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = _fetch(round(lat, 2), round(lng, 2))
    cache.set(key, result, timeout=current_app.config["WEATHER_CACHE_TTL"])
    return result


def place_label(address: str | None, default: str = "Durban") -> str:
    """A short place name for the card, from the saved address ("79 Mansfield Road, Berea, Durban, 4001, South Africa" -> "Durban")."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    while parts and (parts[-1].casefold() == "south africa" or parts[-1].replace(" ", "").isdigit()):
        parts.pop()
    if len(parts) >= 2:
        return parts[-1][:40]
    return default
