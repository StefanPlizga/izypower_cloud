from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .client import IzyClient, ServerUnavailableError
from .statistics import async_insert_hourly_statistics_from_report

_LOGGER = logging.getLogger(__name__)
#_LOGGER.disabled = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username = entry.data.get("username")
    password = entry.data.get("password")
    # Read refresh_period from options first (user can change it), fallback to data, then default
    default_minutes = int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60)
    refresh_period = entry.options.get("refresh_period", entry.data.get("refresh_period", default_minutes))
    
    _LOGGER.info("Setting up Izypower Cloud integration with refresh period: %s minutes", refresh_period)

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

    # Store data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "switch", "number", "button", "select"])

    _LOGGER.debug("Hourly statistics source is coordinator report polling (first successful run each hour)")

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "switch", "number", "button", "select"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
