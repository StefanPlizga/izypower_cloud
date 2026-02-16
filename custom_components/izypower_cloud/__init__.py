from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .client import IzyClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username = entry.data.get("username")
    password = entry.data.get("password")
    # Read refresh_period from options first (user can change it), fallback to data, then default
    default_minutes = int(DEFAULT_SCAN_INTERVAL.total_seconds() / 60)
    refresh_period = entry.options.get("refresh_period", entry.data.get("refresh_period", default_minutes))
    
    _LOGGER.info("Setting up Izypower Cloud integration with refresh period: %s minutes", refresh_period)

    client = IzyClient(hass, username, password)

    async def async_update_data():
        """Fetch all stations and their detailed info."""
        _LOGGER.debug("Fetching stations list (page=1, limit=100)")
        stations_data = await client.async_get_stations(page=1, limit=100)
        
        # Fetch detailed info for each station
        stations_info = {}
        stations_reports = {}
        stations_component = {}
        stations_devices = {}
        now = datetime.now()
        records = stations_data.get("data", {}).get("records", [])
        for record in records:
            station_id = record.get("stationsId")
            if station_id:
                try:
                    station_info = await client.async_get_station_info(component_id=station_id)
                    stations_info[station_id] = station_info
                    _LOGGER.debug("Fetched info for station %s", station_id)
                    
                    # Fetch report data for all time types with appropriate date format
                    stations_reports[station_id] = {}
                    for time_type in ["all", "day", "month", "year"]:
                        try:
                            # Format date based on time_type
                            if time_type in ["all", "day"]:
                                search_time = now.strftime("%Y-%m-%d")
                            elif time_type == "month":
                                search_time = now.strftime("%Y-%m")
                            else:  # year
                                search_time = now.strftime("%Y")
                            
                            report_data = await client.async_get_report(component_id=station_id, date=search_time, time_type=time_type)
                            stations_reports[station_id][time_type] = report_data
                            _LOGGER.debug("Report data for station %s (timeType=%s, searchTime=%s): %s", station_id, time_type, search_time, report_data)
                        except Exception as report_exc:
                            _LOGGER.debug("Failed to fetch report for station %s (timeType=%s): %s", station_id, time_type, report_exc)
                            stations_reports[station_id][time_type] = {}
                    
                    # Fetch component data for PV power values
                    try:
                        current_date = now.strftime("%Y-%m-%d")
                        component_data = await client.async_get_component(component_id=station_id, date=current_date)
                        stations_component[station_id] = component_data
                        _LOGGER.debug("Component data for station %s: %s", station_id, component_data)
                    except Exception as component_exc:
                        _LOGGER.debug("Failed to fetch component data for station %s: %s", station_id, component_exc)
                        stations_component[station_id] = {}
                    
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
                                    _LOGGER.info("Detected battery device %s (ID: %s, SN: %s), fetching battery links", device_name, device_id, device_sn)
                                    try:
                                        battery_links_data = await client.async_get_battery_links(serial_number=device_sn)
                                        if device_id:
                                            stations_devices[station_id]["battery_links"][device_id] = battery_links_data
                                        _LOGGER.info("Battery links data for device ID %s (SN %s): %s", device_id, device_sn, battery_links_data)
                                    except Exception as battery_exc:
                                        _LOGGER.warning("Failed to fetch battery links for device SN %s: %s", device_sn, battery_exc)
                                        if device_id:
                                            stations_devices[station_id]["battery_links"][device_id] = {}
                    except Exception as device_exc:
                        _LOGGER.debug("Failed to fetch device page data for station %s: %s", station_id, device_exc)
                        stations_devices[station_id] = {}
                except Exception as exc:
                    _LOGGER.warning("Failed to fetch info for station %s: %s", station_id, exc)
        
        return {
            "stations": stations_data,
            "stations_info": stations_info,
            "stations_reports": stations_reports,
            "stations_component": stations_component,
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

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
