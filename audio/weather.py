"""Weather module - fetch current weather using free APIs."""

import json
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class WeatherData:
    """Current weather information."""
    temperature_f: float
    feels_like_f: float
    humidity: int
    weather_code: int
    weather_description: str
    wind_speed_mph: float
    is_day: bool
    # Daily forecast
    high_f: float
    low_f: float
    precipitation_chance: int
    location_name: str


# WMO Weather interpretation codes
# https://open-meteo.com/en/docs
WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "light snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def get_location_from_ip() -> Tuple[float, float, str]:
    """
    Get approximate location from IP address.
    Returns (latitude, longitude, city_name).
    Uses ip-api.com (free, no key required).
    """
    try:
        url = "http://ip-api.com/json/?fields=lat,lon,city,regionName"
        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'SmartDisplay/1.0'}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode())
            lat = data.get("lat", 40.23)  # Default to Provo, UT
            lon = data.get("lon", -111.66)
            city = data.get("city", "Unknown")
            region = data.get("regionName", "")
            location_name = f"{city}, {region}" if region else city
            return lat, lon, location_name
    except Exception as e:
        print(f"  [Location lookup failed: {e}, using default]")
        return 40.23, -111.66, "Provo, UT"


def get_weather(lat: Optional[float] = None, lon: Optional[float] = None) -> Optional[WeatherData]:
    """
    Fetch current weather from Open-Meteo API.
    If lat/lon not provided, auto-detects from IP.
    """
    try:
        # Get location if not provided
        if lat is None or lon is None:
            lat, lon, location_name = get_location_from_ip()
        else:
            location_name = "Current Location"

        # Build Open-Meteo API URL
        # Free API, no key required
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            f"weather_code,wind_speed_10m,is_day"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&temperature_unit=fahrenheit"
            f"&wind_speed_unit=mph"
            f"&timezone=auto"
            f"&forecast_days=1"
        )

        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'SmartDisplay/1.0'}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode())

        current = data.get("current", {})
        daily = data.get("daily", {})

        weather_code = current.get("weather_code", 0)

        return WeatherData(
            temperature_f=current.get("temperature_2m", 0),
            feels_like_f=current.get("apparent_temperature", 0),
            humidity=current.get("relative_humidity_2m", 0),
            weather_code=weather_code,
            weather_description=WMO_CODES.get(weather_code, "unknown"),
            wind_speed_mph=current.get("wind_speed_10m", 0),
            is_day=current.get("is_day", 1) == 1,
            high_f=daily.get("temperature_2m_max", [0])[0],
            low_f=daily.get("temperature_2m_min", [0])[0],
            precipitation_chance=daily.get("precipitation_probability_max", [0])[0],
            location_name=location_name,
        )

    except Exception as e:
        print(f"  [Weather fetch failed: {e}]")
        return None


def format_weather_speech(weather: WeatherData) -> str:
    """Format weather data as natural speech."""
    temp = round(weather.temperature_f)
    feels_like = round(weather.feels_like_f)
    high = round(weather.high_f)
    low = round(weather.low_f)

    # Build the response
    parts = []

    # Current conditions
    parts.append(f"It's currently {temp} degrees and {weather.weather_description}")

    # Feels like (only if significantly different)
    if abs(feels_like - temp) >= 5:
        parts.append(f"but feels like {feels_like}")

    # High/low
    parts.append(f"Today's high is {high}, low of {low}")

    # Precipitation chance (only if notable)
    if weather.precipitation_chance >= 30:
        parts.append(f"with a {weather.precipitation_chance} percent chance of precipitation")

    return ". ".join(parts) + "."


def format_temperature_speech(weather: WeatherData) -> str:
    """Format just temperature as speech."""
    temp = round(weather.temperature_f)
    feels_like = round(weather.feels_like_f)

    if abs(feels_like - temp) >= 5:
        return f"It's {temp} degrees, but feels like {feels_like}."
    else:
        return f"It's currently {temp} degrees."
