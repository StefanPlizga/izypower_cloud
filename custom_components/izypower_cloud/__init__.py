from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, STATION_INFO_URL_TEMPLATE, BATTERY_LINKS_URL_TEMPLATE, METER_DATA_URL_TEMPLATE, EVSE_DATA_URL_TEMPLATE
from .client import IzyClient, ServerUnavailableError
from .statistics import async_insert_hourly_statistics_from_report

_LOGGER = logging.getLogger(__name__)
#_LOGGER.disabled = True


class LiveModeManager:
    """Manage station-scoped live mode without changing the normal poll interval."""

    _RENEW_INTERVAL = 90

    def __init__(self, hass: HomeAssistant, client: IzyClient, coordinator: DataUpdateCoordinator):
        self.hass = hass
        self.client = client
        self.coordinator = coordinator
        self._active_until: dict[int, float] = {}
        self._active_batteries: dict[tuple[int, int], tuple[float, list[tuple[int, str]]]] = {}
        self._active_meters: dict[tuple[int, int], tuple[float, str]] = {}
        self._active_evses: dict[tuple[int, int], tuple[float, str]] = {}
        self._station_renew_tasks: dict[int, asyncio.Task] = {}
        self._battery_renew_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._meter_renew_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._evse_renew_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._task = hass.async_create_background_task(
            self._poll(), f"{DOMAIN}_live_mode_poll"
        )

    def is_active(self, station_id: int) -> bool:
        return station_id in self._active_until

    def is_battery_active(self, station_id: int, device_id: int) -> bool:
        return (station_id, device_id) in self._active_batteries

    def is_meter_active(self, station_id: int, device_id: int) -> bool:
        return (station_id, device_id) in self._active_meters

    def is_evse_active(self, station_id: int, device_id: int) -> bool:
        return (station_id, device_id) in self._active_evses

    async def activate(self, station_id: int) -> None:
        await self.client.async_enable_station_live_mode(component_id=station_id)
        self._active_until[station_id] = self.hass.loop.time() + 120
        if station_id not in self._station_renew_tasks:
            self._station_renew_tasks[station_id] = self.hass.async_create_background_task(
                self._renew_station(station_id), f"{DOMAIN}_station_{station_id}_live_mode_renew"
            )
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def deactivate(self, station_id: int) -> None:
        self._active_until.pop(station_id, None)
        task = self._station_renew_tasks.pop(station_id, None)
        if task:
            task.cancel()
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def activate_battery(self, station_id: int, device_id: int, batteries: list[tuple[int, str]]) -> None:
        for _, serial_number in batteries:
            await self.client.async_enable_device_live_mode(serial_number=serial_number)
        self._active_batteries[(station_id, device_id)] = (self.hass.loop.time() + 120, batteries)
        key = (station_id, device_id)
        if key not in self._battery_renew_tasks:
            self._battery_renew_tasks[key] = self.hass.async_create_background_task(
                self._renew_battery(key), f"{DOMAIN}_battery_{device_id}_live_mode_renew"
            )
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def deactivate_battery(self, station_id: int, device_id: int) -> None:
        key = (station_id, device_id)
        self._active_batteries.pop(key, None)
        task = self._battery_renew_tasks.pop(key, None)
        if task:
            task.cancel()
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def activate_meter(self, station_id: int, device_id: int, serial_number: str) -> None:
        await self.client.async_enable_device_live_mode(serial_number=serial_number)
        self._active_meters[(station_id, device_id)] = (self.hass.loop.time() + 120, serial_number)
        key = (station_id, device_id)
        if key not in self._meter_renew_tasks:
            self._meter_renew_tasks[key] = self.hass.async_create_background_task(
                self._renew_meter(key), f"{DOMAIN}_meter_{device_id}_live_mode_renew"
            )
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def deactivate_meter(self, station_id: int, device_id: int) -> None:
        key = (station_id, device_id)
        self._active_meters.pop(key, None)
        task = self._meter_renew_tasks.pop(key, None)
        if task:
            task.cancel()
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def activate_evse(self, station_id: int, device_id: int, serial_number: str) -> None:
        await self.client.async_enable_device_live_mode(serial_number=serial_number)
        self._active_evses[(station_id, device_id)] = (self.hass.loop.time() + 120, serial_number)
        key = (station_id, device_id)
        if key not in self._evse_renew_tasks:
            self._evse_renew_tasks[key] = self.hass.async_create_background_task(
                self._renew_evse(key), f"{DOMAIN}_evse_{device_id}_live_mode_renew"
            )
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def deactivate_evse(self, station_id: int, device_id: int) -> None:
        key = (station_id, device_id)
        self._active_evses.pop(key, None)
        task = self._evse_renew_tasks.pop(key, None)
        if task:
            task.cancel()
        self.coordinator.async_set_updated_data(dict(self.coordinator.data or {}))

    async def _renew_station(self, station_id: int) -> None:
        try:
            while station_id in self._active_until:
                await asyncio.sleep(self._RENEW_INTERVAL)
                if station_id not in self._active_until:
                    return
                try:
                    await self.client.async_enable_station_live_mode(component_id=station_id)
                    self._active_until[station_id] = self.hass.loop.time() + 120
                    _LOGGER.debug("Renewed live mode for station %s", station_id)
                except Exception as exc:
                    _LOGGER.debug("Failed to renew live mode for station %s: %s", station_id, exc)
        except asyncio.CancelledError:
            raise
        finally:
            if self._station_renew_tasks.get(station_id) is asyncio.current_task():
                self._station_renew_tasks.pop(station_id, None)

    async def _renew_battery(self, key: tuple[int, int]) -> None:
        station_id, device_id = key
        try:
            while key in self._active_batteries:
                await asyncio.sleep(self._RENEW_INTERVAL)
                active = self._active_batteries.get(key)
                if not active:
                    return
                _, batteries = active
                try:
                    for _, serial_number in batteries:
                        await self.client.async_enable_device_live_mode(serial_number=serial_number)
                    self._active_batteries[key] = (self.hass.loop.time() + 120, batteries)
                    _LOGGER.debug("Renewed battery live mode for station %s, battery %s", station_id, device_id)
                except Exception as exc:
                    _LOGGER.debug("Failed to renew battery live mode for station %s, battery %s: %s", station_id, device_id, exc)
        except asyncio.CancelledError:
            raise
        finally:
            if self._battery_renew_tasks.get(key) is asyncio.current_task():
                self._battery_renew_tasks.pop(key, None)

    async def _renew_meter(self, key: tuple[int, int]) -> None:
        station_id, device_id = key
        try:
            while key in self._active_meters:
                await asyncio.sleep(self._RENEW_INTERVAL)
                active = self._active_meters.get(key)
                if not active:
                    return
                _, serial_number = active
                try:
                    await self.client.async_enable_device_live_mode(serial_number=serial_number)
                    self._active_meters[key] = (self.hass.loop.time() + 120, serial_number)
                    _LOGGER.debug("Renewed meter live mode for station %s, meter %s", station_id, device_id)
                except Exception as exc:
                    _LOGGER.debug("Failed to renew meter live mode for station %s, meter %s: %s", station_id, device_id, exc)
        except asyncio.CancelledError:
            raise
        finally:
            if self._meter_renew_tasks.get(key) is asyncio.current_task():
                self._meter_renew_tasks.pop(key, None)

    async def _renew_evse(self, key: tuple[int, int]) -> None:
        station_id, device_id = key
        try:
            while key in self._active_evses:
                await asyncio.sleep(self._RENEW_INTERVAL)
                active = self._active_evses.get(key)
                if not active:
                    return
                _, serial_number = active
                try:
                    await self.client.async_enable_device_live_mode(serial_number=serial_number)
                    self._active_evses[key] = (self.hass.loop.time() + 120, serial_number)
                    _LOGGER.debug("Renewed EVSE live mode for station %s, device %s", station_id, device_id)
                except Exception as exc:
                    _LOGGER.debug("Failed to renew EVSE live mode for station %s, device %s: %s", station_id, device_id, exc)
        except asyncio.CancelledError:
            raise
        finally:
            if self._evse_renew_tasks.get(key) is asyncio.current_task():
                self._evse_renew_tasks.pop(key, None)

    async def _poll(self) -> None:
        try:
            while True:
                active_stations = [station_id for station_id in self._active_until if self.is_active(station_id)]
                active_batteries = [key for key in self._active_batteries]
                active_meters = [key for key in self._active_meters]
                active_evses = [key for key in self._active_evses]

                for station_id in active_stations:
                    try:
                        station_info = await self.client.async_get_station_info(component_id=station_id)
                        data = dict(self.coordinator.data or {})
                        stations_info = dict(data.get("stations_info", {}))
                        stations_info[station_id] = station_info
                        data["stations_info"] = stations_info
                        self.coordinator.async_set_updated_data(data)
                        _LOGGER.debug(
                            "Live mode refresh completed for station %s: GET %s",
                            station_id,
                            STATION_INFO_URL_TEMPLATE.format(component_id=station_id),
                        )
                    except Exception as exc:
                        _LOGGER.debug("Live mode station info refresh failed for %s: %s", station_id, exc)

                for (station_id, _), (_, batteries) in [
                    (key, self._active_batteries[key]) for key in active_batteries if key in self._active_batteries
                ]:
                    for battery_device_id, serial_number in batteries:
                        try:
                            battery_links = await self.client.async_get_battery_links(serial_number=serial_number)
                            data = dict(self.coordinator.data or {})
                            stations_devices = dict(data.get("stations_devices", {}))
                            station_devices = dict(stations_devices.get(station_id, {}))
                            battery_links_data = dict(station_devices.get("battery_links", {}))
                            battery_links_data[battery_device_id] = battery_links
                            station_devices["battery_links"] = battery_links_data
                            stations_devices[station_id] = station_devices
                            data["stations_devices"] = stations_devices
                            self.coordinator.async_set_updated_data(data)
                            _LOGGER.debug(
                                "Battery live mode refresh completed for station %s, battery %s: GET %s",
                                station_id,
                                battery_device_id,
                                BATTERY_LINKS_URL_TEMPLATE.format(serial_number=serial_number),
                            )
                        except Exception as exc:
                            _LOGGER.debug(
                                "Battery live mode refresh failed for station %s, battery %s: %s",
                                station_id,
                                battery_device_id,
                                exc,
                            )

                for (station_id, device_id) in active_meters:
                    active_meter = self._active_meters.get((station_id, device_id))
                    if not active_meter:
                        continue
                    _, serial_number = active_meter
                    try:
                        meter_data = await self.client.async_get_meter_data(serial_number=serial_number)
                        data = dict(self.coordinator.data or {})
                        stations_devices = dict(data.get("stations_devices", {}))
                        station_devices = dict(stations_devices.get(station_id, {}))
                        meter_data_by_device = dict(station_devices.get("meter_data", {}))
                        meter_data_by_device[device_id] = meter_data
                        station_devices["meter_data"] = meter_data_by_device
                        stations_devices[station_id] = station_devices
                        data["stations_devices"] = stations_devices
                        self.coordinator.async_set_updated_data(data)
                        _LOGGER.debug(
                            "Meter live mode refresh completed for station %s, meter %s: GET %s",
                            station_id,
                            device_id,
                            METER_DATA_URL_TEMPLATE.format(serial_number=serial_number),
                        )
                    except Exception as exc:
                        _LOGGER.debug(
                            "Meter live mode refresh failed for station %s, meter %s: %s",
                            station_id,
                            device_id,
                            exc,
                        )

                for (station_id, device_id) in active_evses:
                    active_evse = self._active_evses.get((station_id, device_id))
                    if not active_evse:
                        continue
                    _, serial_number = active_evse
                    try:
                        evse_data = await self.client.async_get_evse_data(serial_number=serial_number)
                        data = dict(self.coordinator.data or {})
                        stations_devices = dict(data.get("stations_devices", {}))
                        station_devices = dict(stations_devices.get(station_id, {}))
                        evse_data_by_device = dict(station_devices.get("evse_data", {}))
                        evse_data_by_device[device_id] = evse_data
                        station_devices["evse_data"] = evse_data_by_device
                        stations_devices[station_id] = station_devices
                        data["stations_devices"] = stations_devices
                        self.coordinator.async_set_updated_data(data)
                        _LOGGER.debug(
                            "EVSE live mode refresh completed for station %s, device %s: GET %s",
                            station_id,
                            device_id,
                            EVSE_DATA_URL_TEMPLATE.format(serial_number=serial_number),
                        )
                    except Exception as exc:
                        _LOGGER.debug(
                            "EVSE live mode refresh failed for station %s, device %s: %s",
                            station_id,
                            device_id,
                            exc,
                        )

                await asyncio.sleep(6)
        except asyncio.CancelledError:
            raise

    async def async_stop(self) -> None:
        renew_tasks = [
            *self._station_renew_tasks.values(),
            *self._battery_renew_tasks.values(),
            *self._meter_renew_tasks.values(),
            *self._evse_renew_tasks.values(),
        ]
        for task in renew_tasks:
            task.cancel()
        if renew_tasks:
            await asyncio.gather(*renew_tasks, return_exceptions=True)
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username = entry.data.get("username")
    password = entry.data.get("password")
    # Read refresh_period from options first (user can change it), fallback to data, then default
    default_minutes = int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60)
    refresh_period = entry.options.get("refresh_period", entry.data.get("refresh_period", default_minutes))
    
    _LOGGER.info("Setting up Isypower Cloud integration with refresh period: %s minutes", refresh_period)

    client = IzyClient(hass, username, password)
    last_hourly_stats_slot_by_station: dict[int, str] = {}

    async def async_update_data():
        """Fetch all stations and their detailed info."""
        try:
            _LOGGER.debug("Fetching stations list (page=1, limit=100)")
            stations_data = await client.async_get_stations(page=1, limit=100)
        except ServerUnavailableError as exc:
            # Server is temporarily unavailable - log once at info level and raise UpdateFailed
            # This prevents HA from logging repeated errors while allowing sensors to show unavailable
            _LOGGER.info("Server temporarily unavailable, will retry on next refresh: %s", exc)
            raise UpdateFailed(f"Server temporarily unavailable: {exc}") from exc
        
        # Fetch detailed info for each station
        stations_info = {}
        stations_reports = {}
        stations_component = {}
        stations_layout_power = {}
        stations_devices = {}
        now = datetime.now()
        now_local = dt_util.now().replace(second=0, microsecond=0)
        can_run_hourly_import = now_local.minute >= 10
        prev_hour_local = now_local.replace(minute=0) - timedelta(hours=1)
        prev_hour_slot_key = prev_hour_local.strftime("%Y-%m-%d %H")
        prev_hour_slot_utc = dt_util.as_utc(prev_hour_local)
        if now_local.minute < 10:
            _LOGGER.debug(
                "Skipping hourly statistics import before H+10 (now_local=%s, minute=%s)",
                now_local.isoformat(),
                now_local.minute,
            )
        records = stations_data.get("data", {}).get("records", [])
        for record in records:
            station_id = record.get("stationsId")
            if station_id:
                try:
                    station_info = await client.async_get_station_info(component_id=station_id)
                    stations_info[station_id] = station_info
                    _LOGGER.debug("Fetched info for station %s", station_id)
                    
                    # Fetch report data for all time types with dates aligned to the
                    # hour slot being populated.
                    stations_reports[station_id] = {}
                    for time_type in ["all", "day", "month", "year"]:
                        try:
                            # Use the previous hour's period so boundary hours use the
                            # correct day/month/year payloads.
                            if time_type == "day":
                                search_time = prev_hour_local.strftime("%Y-%m-%d")
                            elif time_type == "all":
                                search_time = prev_hour_local.strftime("%Y-%m-%d")
                            elif time_type == "month":
                                search_time = prev_hour_local.strftime("%Y-%m")
                            else:  # year
                                search_time = prev_hour_local.strftime("%Y")
                            
                            report_data = await client.async_get_report(component_id=station_id, date=search_time, time_type=time_type)
                            stations_reports[station_id][time_type] = report_data
                            _LOGGER.debug(
                                "Report data for station %s (timeType=%s, searchTime=%s): %s",
                                station_id,
                                time_type,
                                search_time,
                                report_data,
                            )

                        except Exception as report_exc:
                            _LOGGER.debug("Failed to fetch report for station %s (timeType=%s): %s", station_id, time_type, report_exc)
                            stations_reports[station_id][time_type] = {}

                    if can_run_hourly_import and last_hourly_stats_slot_by_station.get(station_id) != prev_hour_slot_key:
                        station_name = record.get("stationName", str(station_id))
                        _LOGGER.debug(
                            "Calling async_insert_hourly_statistics_from_report: station=%s station_name=%s slot=%s report_data_by_period=%s",
                            station_id,
                            station_name,
                            prev_hour_slot_utc.isoformat(),
                            stations_reports[station_id],
                        )
                        inserted = await async_insert_hourly_statistics_from_report(
                            hass,
                            station_id,
                            station_name,
                            stations_reports[station_id],
                            prev_hour_slot_utc,
                        )
                        if inserted:
                            last_hourly_stats_slot_by_station[station_id] = prev_hour_slot_key
                            _LOGGER.debug(
                                "Hourly statistics populated from coordinator report for station %s slot %s",
                                station_id,
                                prev_hour_slot_key,
                            )
                    
                    # Fetch component data for PV power values
                    try:
                        current_date = now.strftime("%Y-%m-%d")
                        component_data = await client.async_get_component(component_id=station_id, date=current_date)
                        stations_component[station_id] = component_data
                        _LOGGER.debug("Component data for station %s: %s", station_id, component_data)
                    except Exception as component_exc:
                        _LOGGER.debug("Failed to fetch component data for station %s: %s", station_id, component_exc)
                        stations_component[station_id] = {}

                    # Fetch layout power data for CT channels
                    try:
                        current_date = now.strftime("%Y-%m-%d")
                        layout_power_data = await client.async_get_layout_power(component_id=station_id, date=current_date, is_v2=True)
                        stations_layout_power[station_id] = layout_power_data
                        _LOGGER.debug("Layout power data for station %s: %s", station_id, layout_power_data)
                    except Exception as layout_exc:
                        _LOGGER.debug("Failed to fetch layout power data for station %s: %s", station_id, layout_exc)
                        stations_layout_power[station_id] = {}
                    
                    # Fetch device page data for device online state
                    try:
                        device_page_data = await client.async_get_device_page(component_id=station_id, device_type="all", page=1, limit=100)
                        stations_devices[station_id] = device_page_data
                        _LOGGER.debug("Device page data for station %s: %s", station_id, device_page_data)
                        
                        # Get device type mapping from station info to identify battery devices
                        device_type_mapping = {}
                        device_types_enum = station_info.get("deviceTypes", [])
                        for device_type_info in device_types_enum:
                            type_code = device_type_info.get("value")
                            type_name = device_type_info.get("name")
                            if type_code and type_name:
                                device_type_mapping[type_code] = type_name
                        
                        # Fetch WiFi data for each device with a serial number
                        device_records = device_page_data.get("data", {}).get("records", [])
                        stations_devices[station_id]["wifi_data"] = {}
                        stations_devices[station_id]["battery_links"] = {}
                        stations_devices[station_id]["battery_cmd"] = {}
                        stations_devices[station_id]["temp_data"] = {}
                        stations_devices[station_id]["meter_base_info"] = {}
                        stations_devices[station_id]["meter_data"] = {}
                        stations_devices[station_id]["evse_data"] = {}
                        stations_devices[station_id]["evse_cmd"] = {}
                        stations_devices[station_id]["evse_intelligent"] = {}
                        stations_devices[station_id]["evse_derate"] = {}
                        stations_devices[station_id]["evse_priority"] = {}
                        
                        for device_record in device_records:
                            device_sn = device_record.get("sn") or device_record.get("serialNumber")
                            device_id = device_record.get("deviceId")
                            
                            if device_sn:
                                # Fetch WiFi data
                                try:
                                    wifi_data = await client.async_get_device_wifi(serial_number=device_sn)
                                    stations_devices[station_id]["wifi_data"][device_sn] = wifi_data
                                    _LOGGER.debug("WiFi data for device SN %s: %s", device_sn, wifi_data)
                                except Exception as wifi_exc:
                                    _LOGGER.debug("Failed to fetch WiFi data for device SN %s: %s", device_sn, wifi_exc)
                                    stations_devices[station_id]["wifi_data"][device_sn] = {}
                                
                                # Check if this is a battery device and fetch battery links
                                device_type_code = device_record.get("deviceType")
                                device_type_name = device_type_mapping.get(device_type_code, "").lower()
                                device_name = device_record.get("deviceName", "Unknown")
                                
                                _LOGGER.debug("Checking device %s (ID: %s, SN: %s): deviceType='%s', deviceTypeName='%s'", 
                                             device_name, device_id, device_sn, device_type_code, device_type_name)
                                
                                if "battery" in device_type_name or device_type_code == "battery":
                                    _LOGGER.debug("Detected battery device %s (ID: %s, SN: %s), fetching battery links", device_name, device_id, device_sn)
                                    try:
                                        battery_links_data = await client.async_get_battery_links(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["battery_links"][device_id] = battery_links_data
                                        _LOGGER.debug("Battery links data for device ID %s (SN %s): %s", device_id, device_sn, battery_links_data)
                                    except ServerUnavailableError as battery_exc:
                                        _LOGGER.debug("Server unavailable when fetching battery links for device SN %s: %s", device_sn, battery_exc)
                                        if device_id:
                                            stations_devices[station_id]["battery_links"][device_id] = {}
                                    except Exception as battery_exc:
                                        _LOGGER.warning("Failed to fetch battery links for device SN %s: %s", device_sn, battery_exc)
                                        if device_id:
                                            stations_devices[station_id]["battery_links"][device_id] = {}
                                    
                                    # Fetch battery cmd data (min_soc and other settings)
                                    _LOGGER.debug("Fetching battery cmd data for device %s (ID: %s, SN: %s)", device_name, device_id, device_sn)
                                    try:
                                        battery_cmd_data = await client.async_get_battery_cmd(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["battery_cmd"][device_id] = battery_cmd_data
                                        _LOGGER.debug("Battery cmd data for device ID %s (SN %s): %s", device_id, device_sn, battery_cmd_data)
                                    except ServerUnavailableError as cmd_exc:
                                        _LOGGER.debug("Server unavailable when fetching battery cmd for device SN %s: %s", device_sn, cmd_exc)
                                        if device_id:
                                            stations_devices[station_id]["battery_cmd"][device_id] = {}
                                    except Exception as cmd_exc:
                                        _LOGGER.warning("Failed to fetch battery cmd for device SN %s: %s", device_sn, cmd_exc)
                                        if device_id:
                                            stations_devices[station_id]["battery_cmd"][device_id] = {}
                                
                                # Check if this is a vm device and fetch temperature data
                                if device_type_code == "vm":
                                    _LOGGER.debug("Detected vm device %s (ID: %s, SN: %s), fetching temperature data", device_name, device_id, device_sn)
                                    try:
                                        current_date = now.strftime("%Y-%m-%d")
                                        temp_data = await client.async_get_device_temp(serial_number=device_sn, date=current_date)
                                        if device_id:
                                            stations_devices[station_id]["temp_data"][device_id] = temp_data
                                        _LOGGER.debug("Temperature data for device ID %s (SN %s): %s", device_id, device_sn, temp_data)
                                    except ServerUnavailableError as temp_exc:
                                        _LOGGER.debug("Server unavailable when fetching temperature data for device SN %s: %s", device_sn, temp_exc)
                                        if device_id:
                                            stations_devices[station_id]["temp_data"][device_id] = {}
                                    except Exception as temp_exc:
                                        _LOGGER.warning("Failed to fetch temperature data for device SN %s: %s", device_sn, temp_exc)
                                        if device_id:
                                            stations_devices[station_id]["temp_data"][device_id] = {}
                                
                                # Check if this is a meter device and fetch base info for injection control
                                if device_type_code == "meter":
                                    _LOGGER.debug("Detected meter device %s (ID: %s, SN: %s), fetching base info", device_name, device_id, device_sn)
                                    try:
                                        meter_base_info = await client.async_get_meter_base_info(device_id=device_id)
                                        if device_id:
                                            stations_devices[station_id]["meter_base_info"][device_id] = meter_base_info
                                        _LOGGER.debug("Meter base info for device ID %s (SN %s): %s", device_id, device_sn, meter_base_info)
                                    except ServerUnavailableError as meter_exc:
                                        _LOGGER.debug("Server unavailable when fetching meter base info for device SN %s: %s", device_sn, meter_exc)
                                        if device_id:
                                            stations_devices[station_id]["meter_base_info"][device_id] = {}
                                    except Exception as meter_exc:
                                        _LOGGER.warning("Failed to fetch meter base info for device SN %s: %s", device_sn, meter_exc)
                                        if device_id:
                                            stations_devices[station_id]["meter_base_info"][device_id] = {}

                                    try:
                                        meter_data = await client.async_get_meter_data(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["meter_data"][device_id] = meter_data
                                        _LOGGER.debug("Meter data for device ID %s (SN %s): %s", device_id, device_sn, meter_data)
                                    except ServerUnavailableError as meter_data_exc:
                                        _LOGGER.debug("Server unavailable when fetching meter data for SN %s: %s", device_sn, meter_data_exc)
                                        if device_id:
                                            stations_devices[station_id]["meter_data"][device_id] = {}
                                    except Exception as meter_data_exc:
                                        _LOGGER.warning("Failed to fetch meter data for SN %s: %s", device_sn, meter_data_exc)
                                        if device_id:
                                            stations_devices[station_id]["meter_data"][device_id] = {}

                                if device_type_code == "evse":
                                    try:
                                        evse_data = await client.async_get_evse_data(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["evse_data"][device_id] = evse_data
                                        _LOGGER.debug("EVSE data for device ID %s (SN %s): %s", device_id, device_sn, evse_data)
                                    except ServerUnavailableError as evse_exc:
                                        _LOGGER.debug("Server unavailable when fetching EVSE data for SN %s: %s", device_sn, evse_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_data"][device_id] = {}
                                    except Exception as evse_exc:
                                        _LOGGER.warning("Failed to fetch EVSE data for SN %s: %s", device_sn, evse_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_data"][device_id] = {}

                                if device_type_code == "evse":
                                    _LOGGER.debug(
                                        "Detected EVSE device %s (ID: %s, SN: %s), fetching command data",
                                        device_name,
                                        device_id,
                                        device_sn,
                                    )
                                    try:
                                        evse_cmd_data = await client.async_get_battery_cmd(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["evse_cmd"][device_id] = evse_cmd_data
                                        _LOGGER.debug(
                                            "EVSE command data cached for station %s, device ID %s (SN %s): %s",
                                            station_id,
                                            device_id,
                                            device_sn,
                                            evse_cmd_data,
                                        )
                                    except ServerUnavailableError as evse_cmd_exc:
                                        _LOGGER.debug("Server unavailable when fetching EVSE command data for SN %s: %s", device_sn, evse_cmd_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_cmd"][device_id] = {}
                                    except Exception as evse_cmd_exc:
                                        _LOGGER.warning("Failed to fetch EVSE command data for SN %s: %s", device_sn, evse_cmd_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_cmd"][device_id] = {}

                                    try:
                                        evse_intelligent = await client.async_get_evse_intelligent_value(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["evse_intelligent"][device_id] = evse_intelligent
                                        _LOGGER.debug("EVSE intelligent value for device ID %s (SN %s): %s", device_id, device_sn, evse_intelligent)
                                    except ServerUnavailableError as evse_int_exc:
                                        _LOGGER.debug("Server unavailable when fetching EVSE intelligent value for SN %s: %s", device_sn, evse_int_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_intelligent"][device_id] = {}
                                    except Exception as evse_int_exc:
                                        _LOGGER.warning("Failed to fetch EVSE intelligent value for SN %s: %s", device_sn, evse_int_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_intelligent"][device_id] = {}

                                    try:
                                        evse_derate = await client.async_get_evse_power_derate(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["evse_derate"][device_id] = evse_derate
                                        _LOGGER.debug("EVSE power derate for device ID %s (SN %s): %s", device_id, device_sn, evse_derate)
                                    except ServerUnavailableError as evse_derate_exc:
                                        _LOGGER.debug("Server unavailable when fetching EVSE power derate for SN %s: %s", device_sn, evse_derate_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_derate"][device_id] = {}
                                    except Exception as evse_derate_exc:
                                        _LOGGER.warning("Failed to fetch EVSE power derate for SN %s: %s", device_sn, evse_derate_exc)
                                        if device_id:
                                            stations_devices[station_id]["evse_derate"][device_id] = {}

                                    try:
                                        evse_priority = await client.async_get_evse_priority(station_id=station_id)
                                        stations_devices[station_id]["evse_priority"] = evse_priority
                                        _LOGGER.debug("EVSE priority for station %s: %s", station_id, evse_priority)
                                    except ServerUnavailableError as evse_prio_exc:
                                        _LOGGER.debug("Server unavailable when fetching EVSE priority for station %s: %s", station_id, evse_prio_exc)
                                    except Exception as evse_prio_exc:
                                        _LOGGER.warning("Failed to fetch EVSE priority for station %s: %s", station_id, evse_prio_exc)
                        
                        # Fetch device upgrade information for the station
                        try:
                            upgrade_data = await client.async_get_device_upgrade(station_id=station_id)
                            stations_devices[station_id]["upgrade_data"] = upgrade_data
                            _LOGGER.debug("Device upgrade data for station %s: %s", station_id, upgrade_data)
                        except Exception as upgrade_exc:
                            _LOGGER.debug("Failed to fetch device upgrade data for station %s: %s", station_id, upgrade_exc)
                            stations_devices[station_id]["upgrade_data"] = {}
                    except Exception as device_exc:
                        _LOGGER.debug("Failed to fetch device page data for station %s: %s", station_id, device_exc)
                        stations_devices[station_id] = {}
                except ServerUnavailableError as exc:
                    _LOGGER.debug("Server unavailable when fetching info for station %s: %s", station_id, exc)
                except Exception as exc:
                    _LOGGER.warning("Failed to fetch info for station %s: %s", station_id, exc)
        
        return {
            "stations": stations_data,
            "stations_info": stations_info,
            "stations_reports": stations_reports,
            "stations_component": stations_component,
            "stations_layout_power": stations_layout_power,
            "stations_devices": stations_devices,
        }

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_data",
        update_method=async_update_data,
        update_interval=timedelta(minutes=refresh_period),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()
    live_mode = LiveModeManager(hass, client, coordinator)

    # Store data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "live_mode": live_mode,
    }

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "switch", "number", "button", "select"])

    _LOGGER.debug("Hourly statistics source is coordinator report polling (first successful run each hour)")

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.async_on_unload(live_mode.async_stop)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "switch", "number", "button", "select"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
