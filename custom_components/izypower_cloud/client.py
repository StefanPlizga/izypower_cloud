import asyncio
import time
import json
import base64
import random
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import LOGIN_URL, STATIONS_URL, DEVICE_PAGE_URL_TEMPLATE, COMPONENT_URL_TEMPLATE, STATION_INFO_URL_TEMPLATE, REPORT_URL_TEMPLATE, LAYOUT_POWER_URL_TEMPLATE, DEVICE_WIFI_URL_TEMPLATE, BATTERY_LINKS_URL_TEMPLATE, DEVICE_TEMP_URL_TEMPLATE, DEVICE_UPGRADE_URL_TEMPLATE, METER_BASE_INFO_URL_TEMPLATE, METER_CONTROL_URL_TEMPLATE, BATTERY_LED_URL_TEMPLATE, BATTERY_CMD_URL_TEMPLATE, BATTERY_TOGGLE_OFFGRID_URL_TEMPLATE, BATTERY_OFFGRID_URL_TEMPLATE, BATTERY_TOGGLE_FULLCHARGE_DAYS_URL_TEMPLATE, BATTERY_FULLCHARGE_DAYS_URL_TEMPLATE, BATTERY_FULLCHARGE_TIME_URL_TEMPLATE, BATTERY_MIN_SOC_URL_TEMPLATE, BATTERY_POWER_URL_TEMPLATE, BATTERY_MODE_URL_TEMPLATE, BATTERY_MANUAL_MODE_VALUE_URL_TEMPLATE, TOKEN_HEADER, APP_PLATFORM_HEADER, DEBUG_LOG_REQUESTS
import logging

_LOGGER = logging.getLogger(__name__)


class ServerUnavailableError(Exception):
    """Exception raised when the server is temporarily unavailable (502, 503, 504, timeouts)."""
    pass


class IzyClient:
    def __init__(self, hass: HomeAssistant, username: str, password: str):
        self.hass = hass
        self._username = username
        self._password = password
        self._token: Optional[str] = None
        self._expiry: Optional[float] = None

    @property
    def token(self) -> Optional[str]:
        return self._token

    def _get_language_header(self) -> str:
        """Get Accept-Language header based on HA language setting."""
        lang = (self.hass.config.language or "en").lower()
        header = "fr" if lang.startswith("fr") else "en"
        _LOGGER.debug("Detected HA language '%s', using Accept-Language: %s", lang, header)
        return header

    def _decode_jwt_exp(self, token: str) -> Optional[float]:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            padding = "=" * ((4 - len(payload_b64) % 4) % 4)
            payload_b64 += padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            exp = payload.get("exp")
            if exp:
                return float(exp)
        except Exception as exc:
            _LOGGER.debug("Failed decoding JWT exp: %s", exc)
            return None
        return None

    async def async_login(self) -> None:
        session = async_get_clientsession(self.hass)
        body = {"username": self._username, "password": self._password}
        max_attempts = 2
        backoff_base = 1.0
        for attempt in range(1, max_attempts + 1):
            try:
                headers = {"Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.post(LOGIN_URL, json=body, headers=headers, timeout=15) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Login response (status %s)", resp.status)
                    else:
                        _LOGGER.debug("Login response received for user %s", self._username)
                    try:
                        data = json.loads(text)
                    except Exception:
                        _LOGGER.error("Login response not JSON")
                        raise

                    token = None
                    if isinstance(data, dict):
                        token = data.get("data", {}).get("token")
                    if not token:
                        _LOGGER.error("Login failed or token missing")
                        raise Exception("Login failed: no token returned")
                    self._token = token
                    exp = self._decode_jwt_exp(token)
                    if exp:
                        self._expiry = float(exp)
                    else:
                        self._expiry = time.time() + 600
                    _LOGGER.debug("Obtained token, expires at %s", self._expiry)
                    return
            except asyncio.TimeoutError:
                _LOGGER.warning("Login request timed out (attempt %s/%s)", attempt, max_attempts)
            except Exception:
                _LOGGER.exception("Login failed (attempt %s/%s)", attempt, max_attempts)

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

        raise Exception("Login failed after retries")

    def _token_is_valid(self) -> bool:
        if not self._token or not self._expiry:
            return False
        return time.time() < (self._expiry - 10)

    async def async_get_stations(self, page: int = 1, limit: int = 100) -> Dict[str, Any]:
        """Fetch paged list of power stations."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                # ensure token valid
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = f"{STATIONS_URL}?page={page}&limit={limit}"
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Stations response (status %s): %s", resp.status, text)
                    else:
                        _LOGGER.debug("Stations response received (page=%s, limit=%s)", page, limit)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching stations; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching stations (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching stations: status %s body %s", resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from stations: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching stations (attempt %s/%s)", attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching stations (attempt %s/%s): %s", attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_device_page(self, component_id: int, device_type: str = "all", page: int = 1, limit: int = 100) -> Dict[str, Any]:
        """Fetch device page info for a power station."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = DEVICE_PAGE_URL_TEMPLATE.format(component_id=component_id, device_type=device_type, page=page, limit=limit)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Device page response (status %s): %s", resp.status, text)
                    else:
                        _LOGGER.debug("Device page response received for station %s", component_id)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching device page; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching device page (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching device page %s: status %s body %s", component_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from device page: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching device page %s (attempt %s/%s)", component_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching device page %s (attempt %s/%s): %s", component_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_component(self, component_id: int, date: str) -> Dict[str, Any]:
        """Fetch component data for a device."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = COMPONENT_URL_TEMPLATE.format(component_id=component_id, date=date)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Component response (status %s): %s", resp.status, text)
                    else:
                        _LOGGER.debug("Component response received for station %s", component_id)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching component; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching component (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching component %s: status %s body %s", component_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from component: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching component %s (attempt %s/%s)", component_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching component %s (attempt %s/%s): %s", component_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_layout_power(self, component_id: int, date: str, is_v2: bool = True) -> Dict[str, Any]:
        """Fetch layout power data used for CT channels."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = LAYOUT_POWER_URL_TEMPLATE.format(component_id=component_id, date=date, is_v2=str(is_v2).lower())
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Layout power response (status %s): %s", resp.status, text)
                    else:
                        _LOGGER.debug("Layout power response received for station %s", component_id)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching layout power; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching layout power (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching layout power %s: status %s body %s", component_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from layout power: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching layout power %s (attempt %s/%s)", component_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching layout power %s (attempt %s/%s): %s", component_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_station_info(self, component_id: int) -> Dict[str, Any]:
        """Fetch station info including device types enumeration."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = STATION_INFO_URL_TEMPLATE.format(component_id=component_id)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Station info response (status %s): %s", resp.status, text)
                    else:
                        _LOGGER.debug("Station info response received for station %s", component_id)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching station info; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching station info (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching station info %s: status %s body %s", component_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from station info: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching station info %s (attempt %s/%s)", component_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching station info %s (attempt %s/%s): %s", component_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_report(self, component_id: int, date: str, time_type: str = "day") -> Dict[str, Any]:
        """Fetch report data for a station."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = REPORT_URL_TEMPLATE.format(component_id=component_id, date=date, time_type=time_type)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Report response for station %s, timeType=%s (status %s): %s", component_id, time_type, resp.status, text)
                    else:
                        _LOGGER.debug("Report response received for station %s, timeType=%s", component_id, time_type)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching report; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching report (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching report %s: status %s body %s", component_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from report: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching report %s (attempt %s/%s)", component_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching report %s (attempt %s/%s): %s", component_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_device_wifi(self, serial_number: str) -> Dict[str, Any]:
        """Fetch WiFi information for a device by serial number."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = DEVICE_WIFI_URL_TEMPLATE.format(serial_number=serial_number)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Device WiFi response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Device WiFi response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching device WiFi; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching device WiFi (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching device WiFi %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from device WiFi: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching device WiFi %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching device WiFi %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_battery_links(self, serial_number: str) -> Dict[str, Any]:
        """Fetch battery link information for a battery device by serial number."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_LINKS_URL_TEMPLATE.format(serial_number=serial_number)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery links response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery links response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching battery links; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching battery links (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching battery links %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery links: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching battery links %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching battery links %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_device_temp(self, serial_number: str, date: str) -> Dict[str, Any]:
        """Fetch device temperature data."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = DEVICE_TEMP_URL_TEMPLATE.format(serial_number=serial_number, date=date)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Device temp response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Device temp response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching device temp; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching device temp (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching device temp %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from device temp: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching device temp %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching device temp %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_device_upgrade(self, station_id: int) -> Dict[str, Any]:
        """Fetch device upgrade information for a station."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = DEVICE_UPGRADE_URL_TEMPLATE.format(station_id=station_id)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Device upgrade response for station %s (status %s): %s", station_id, resp.status, text)
                    else:
                        _LOGGER.debug("Device upgrade response received for station %s", station_id)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching device upgrade; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching device upgrade (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching device upgrade %s: status %s body %s", station_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from device upgrade: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching device upgrade %s (attempt %s/%s)", station_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching device upgrade %s (attempt %s/%s): %s", station_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_meter_base_info(self, device_id: int) -> Dict[str, Any]:
        """Fetch meter base information including injection control settings."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = METER_BASE_INFO_URL_TEMPLATE.format(device_id=device_id)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Meter base info response for device %s (status %s): %s", device_id, resp.status, text)
                    else:
                        _LOGGER.debug("Meter base info response received for device %s", device_id)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching meter base info; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching meter base info (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching meter base info %s: status %s body %s", device_id, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from meter base info: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching meter base info %s (attempt %s/%s)", device_id, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching meter base info %s (attempt %s/%s): %s", device_id, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_meter_control(self, serial_number: str, is_control: bool, feed_threshold: int) -> Dict[str, Any]:
        """Set meter injection control settings."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = METER_CONTROL_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"isControl": is_control, "feedThreshold": feed_threshold}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting meter control for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Meter control response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Meter control response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting meter control; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting meter control (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting meter control %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from meter control: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting meter control %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting meter control %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_battery_led(self, serial_number: str, value: int) -> Dict[str, Any]:
        """Set battery LED state (0=off, 1=on)."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_LED_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery LED for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery LED response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery LED response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery LED; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery LED (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery LED %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery LED: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery LED %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery LED %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_get_battery_cmd(self, serial_number: str) -> Dict[str, Any]:
        """Fetch battery command/settings data including min_soc."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_CMD_URL_TEMPLATE.format(serial_number=serial_number)
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                async with session.get(url, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery cmd response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery cmd response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when fetching battery cmd; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when fetching battery cmd (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed fetching battery cmd %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery cmd: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out fetching battery cmd %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error fetching battery cmd %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_battery_min_soc(self, serial_number: str, value: int) -> Dict[str, Any]:
        """Set battery minimum state of charge (discharge limit)."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_MIN_SOC_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery min_soc for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery min_soc response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery min_soc response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery min_soc; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery min_soc (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery min_soc %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery min_soc: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery min_soc %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery min_soc %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_battery_power(
        self, 
        serial_number: str, 
        max_in_power: int, 
        max_out_power: int, 
        type: str, 
        is_cluster: bool
    ) -> Dict[str, Any]:
        """Set battery maximum charge/discharge power."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_POWER_URL_TEMPLATE.format(serial_number=serial_number)
                body = {
                    "max_in_power": max_in_power,
                    "max_out_power": max_out_power,
                    "type": type,
                    "isCluster": is_cluster
                }
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery power for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery power response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery power response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery power; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery power (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery power %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery power: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery power %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery power %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_battery_mode(self, serial_number: str, ctr_mode: int) -> Dict[str, Any]:
        """Set battery control mode."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_MODE_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"ctr_mode": ctr_mode}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery mode for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery mode response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery mode response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery mode; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery mode (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery mode %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery mode: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery mode %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery mode %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_battery_manual_mode_value(self, serial_number: str, mode: int, power: int) -> Dict[str, Any]:
        """Set battery manual mode and power in a single request.
        
        When mode is 0 (Standby), power must be set to 0.
        When mode is 1 (Charge) or 2 (Discharge), power is the value in watts.
        """
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_MANUAL_MODE_VALUE_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"mode": mode, "power": power}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery manual mode/power for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery manual mode/power response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery manual mode/power response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery manual mode/power; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery manual mode/power (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery manual mode/power %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery manual mode/power: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery manual mode/power %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery manual mode/power %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_toggle_offgrid(self, serial_number: str, value: bool) -> Dict[str, Any]:
        """Toggle battery off-grid (backup outlet) mode."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_TOGGLE_OFFGRID_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Toggling battery off-grid for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery toggle off-grid response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery toggle off-grid response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when toggling battery off-grid; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when toggling battery off-grid (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed toggling battery off-grid %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery toggle off-grid: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out toggling battery off-grid %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error toggling battery off-grid %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_offgrid_mode(self, serial_number: str, value: int) -> Dict[str, Any]:
        """Set battery off-grid mode (1=Invester, 2=Always, 3=When power cut)."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_OFFGRID_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery off-grid mode for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery off-grid mode response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery off-grid mode response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery off-grid mode; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery off-grid mode (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery off-grid mode %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery off-grid mode: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery off-grid mode %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery off-grid mode %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_toggle_fullcharge_days(self, serial_number: str, value: bool) -> Dict[str, Any]:
        """Toggle battery calibration (full charge) mode."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_TOGGLE_FULLCHARGE_DAYS_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Toggling battery calibration for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery toggle calibration response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery toggle calibration response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when toggling battery calibration; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when toggling battery calibration (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed toggling battery calibration %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery toggle calibration: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out toggling battery calibration %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error toggling battery calibration %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_fullcharge_days(self, serial_number: str, value: int) -> Dict[str, Any]:
        """Set battery calibration interval in days."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_FULLCHARGE_DAYS_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery calibration interval for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery calibration interval response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery calibration interval response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery calibration interval; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery calibration interval (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery calibration interval %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery calibration interval: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery calibration interval %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery calibration interval %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

    async def async_set_fullcharge_time(self, serial_number: str, value: str) -> Dict[str, Any]:
        """Set battery calibration time (HH:MM format)."""
        max_attempts = 3
        backoff_base = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                if not self._token_is_valid():
                    await self.async_login()

                session = async_get_clientsession(self.hass)
                url = BATTERY_FULLCHARGE_TIME_URL_TEMPLATE.format(serial_number=serial_number)
                body = {"value": value}
                headers = {TOKEN_HEADER: self._token, "Accept-Language": self._get_language_header(), "app-platform": APP_PLATFORM_HEADER}
                
                _LOGGER.info("Setting battery calibration time for %s: body=%s", serial_number, body)
                
                async with session.post(url, json=body, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    if DEBUG_LOG_REQUESTS:
                        _LOGGER.debug("Battery calibration time response for SN %s (status %s): %s", serial_number, resp.status, text)
                    else:
                        _LOGGER.debug("Battery calibration time response received for SN %s", serial_number)

                    if resp.status == 401:
                        _LOGGER.debug("Unauthorized (401) when setting battery calibration time; will re-login (attempt %s/%s)", attempt, max_attempts)
                        await self.async_login()
                        raise Exception("Unauthorized")

                    if 500 <= resp.status < 600:
                        _LOGGER.debug("Server error %s when setting battery calibration time (attempt %s/%s)", resp.status, attempt, max_attempts)
                        raise ServerUnavailableError(f"Server returned {resp.status}")

                    if resp.status != 200:
                        _LOGGER.error("Failed setting battery calibration time %s: status %s body %s", serial_number, resp.status, text)
                        raise Exception(f"HTTP {resp.status}")

                    try:
                        return json.loads(text)
                    except Exception:
                        _LOGGER.error("Invalid JSON from battery calibration time: %s", text)
                        raise

            except asyncio.TimeoutError:
                _LOGGER.debug("Request timed out setting battery calibration time %s (attempt %s/%s)", serial_number, attempt, max_attempts)
                if attempt == max_attempts:
                    raise ServerUnavailableError("Request timed out after all retries")
            except ServerUnavailableError:
                if attempt == max_attempts:
                    raise
            except Exception as exc:
                _LOGGER.debug("Error setting battery calibration time %s (attempt %s/%s): %s", serial_number, attempt, max_attempts, exc)
                if attempt == max_attempts:
                    raise

            if attempt < max_attempts:
                jitter = random.random() * 0.5
                wait = backoff_base * (2 ** (attempt - 1)) + jitter
                await asyncio.sleep(wait)

