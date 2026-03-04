from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen


DEFAULT_LOCATION = {"name": "Toronto", "latitude": 43.6532, "longitude": -79.3832}


WEATHER_CODE_LABELS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
}


def _http_get_json(url: str) -> dict:
    with urlopen(url, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _resolve_location(location_name: str | None) -> dict:
    if not location_name:
        return DEFAULT_LOCATION

    params = urlencode({"name": location_name, "count": 1, "language": "en", "format": "json"})
    url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
    data = _http_get_json(url)
    results = data.get("results") or []
    if not results:
        return DEFAULT_LOCATION

    place = results[0]
    return {
        "name": place.get("name", location_name),
        "latitude": place["latitude"],
        "longitude": place["longitude"],
    }


def check_weather(location_name: str | None = None) -> str:
    location = _resolve_location(location_name)

    params = urlencode(
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current_weather": "true",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "forecast_days": 1,
            "timezone": "auto",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    data = _http_get_json(url)

    current = data.get("current_weather", {})
    daily = data.get("daily", {})
    weather_code = current.get("weathercode")

    summary = {
        "location": location["name"],
        "current_temperature_c": current.get("temperature"),
        "current_windspeed_kmh": current.get("windspeed"),
        "current_condition": WEATHER_CODE_LABELS.get(weather_code, f"Code {weather_code}"),
        "today_max_c": (daily.get("temperature_2m_max") or [None])[0],
        "today_min_c": (daily.get("temperature_2m_min") or [None])[0],
        "today_precip_probability_max": (daily.get("precipitation_probability_max") or [None])[0],
    }

    return json.dumps(summary)
