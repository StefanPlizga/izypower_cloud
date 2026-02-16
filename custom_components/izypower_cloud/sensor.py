from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, ENTITY_ID_PREFIX, DISPLAY_NAME_PREFIX

_LOGGER = logging.getLogger(__name__)


# Base classes for factorization

class StationBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for station sensors."""
    
    has_entity_name = True
    
    def __init__(self, coordinator, station_id: int, station_name: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._station_name = station_name
    
    def _get_station_info(self) -> dict:
        """Get fresh station info from coordinator data."""
        coordinator_data = self.coordinator.data or {}
        stations_info = coordinator_data.get("stations_info", {})
        return stations_info.get(self._station_id, {})
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_station_{self._station_id}")},
        }


class StationEnergySensor(StationBaseSensor):
    """Base class for station energy sensors."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    
    def __init__(self, coordinator, station_id: int, station_name: str, 
                 data_category: str, period: str, translation_key: str):
        """Initialize the energy sensor."""
        super().__init__(coordinator, station_id, station_name)
        self._data_category = data_category
        self._period = period
        self._attr_unique_id = f"{station_id}_{translation_key}"
        self._attr_translation_key = translation_key
    
    @property
    def native_value(self):
        """Return the energy value."""
        return self._get_station_info().get("extraData", {}).get(self._data_category, {}).get(self._period, 0)


class StationCalculatedEnergySensor(StationBaseSensor):
    """Base class for calculated energy sensors."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    
    def __init__(self, coordinator, station_id: int, station_name: str, 
                 period: str, translation_key: str, fields: dict):
        """Initialize the calculated sensor."""
        super().__init__(coordinator, station_id, station_name)
        self._period_fields = fields
        self._attr_unique_id = f"{station_id}_{translation_key}"
        self._attr_translation_key = translation_key
    
    @property
    def native_value(self):
        """Return the calculated energy value."""
        extra_data = self._get_station_info().get("extraData", {})
        consumption = extra_data.get("consumption", {}).get(self._period_fields["consumption"], 0) or 0
        battery_discharge = extra_data.get("battery", {}).get(self._period_fields["battery"], 0) or 0
        grid_import = extra_data.get("grid", {}).get(self._period_fields["grid"], 0) or 0
        result = consumption - battery_discharge - grid_import
        return max(0, result)


class StationRateSensor(StationBaseSensor):
    """Base class for station rate sensors."""
    
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    
    def __init__(self, coordinator, station_id: int, station_name: str, 
                 time_type: str, field_name: str, translation_key: str):
        """Initialize the rate sensor."""
        super().__init__(coordinator, station_id, station_name)
        self._time_type = time_type
        self._field_name = field_name
        self._attr_unique_id = f"{station_id}_{translation_key}_{time_type}"
        self._attr_translation_key = f"{translation_key}_{time_type}"
    
    def _get_report_data(self) -> dict:
        """Get fresh report data from coordinator data."""
        coordinator_data = self.coordinator.data or {}
        stations_reports = coordinator_data.get("stations_reports", {})
        station_reports = stations_reports.get(self._station_id, {})
        return station_reports.get(self._time_type, {})
    
    @property
    def native_value(self):
        """Return the rate value."""
        value = self._get_report_data().get(self._field_name)
        if value is not None:
            if isinstance(value, str):
                value = value.rstrip('%')
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        return None


class StationDirectFieldSensor(StationBaseSensor):
    """Base class for station sensors reading direct fields from station_info."""
    
    def __init__(self, coordinator, station_id: int, station_name: str,
                 field_name: str, translation_key: str, device_class, unit: str, state_class, default_value=0):
        """Initialize the direct field sensor."""
        super().__init__(coordinator, station_id, station_name)
        self._field_name = field_name
        self._default_value = default_value
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_station_{station_id}_{translation_key}"
        self._attr_translation_key = translation_key
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_native_unit_of_measurement = unit
    
    @property
    def native_value(self):
        """Return the field value from station info."""
        value = self._get_station_info().get(self._field_name)
        return value if value is not None else self._default_value


class StationLastUpdateSensor(StationBaseSensor):
    """Sensor for station last update timestamp."""
    
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, coordinator, station_id: int, station_name: str):
        """Initialize the last update sensor."""
        super().__init__(coordinator, station_id, station_name)
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_station_{station_id}_last_update"
        self._attr_translation_key = "last_update"
    
    @property
    def native_value(self):
        """Return the last update timestamp as timezone-aware datetime."""
        station_info = self._get_station_info()
        last_update_str = station_info.get("lastUpdate")
        
        if not last_update_str:
            _LOGGER.debug("No lastUpdate field found in station_info for station %s", self._station_id)
            return None
        
        _LOGGER.debug("Raw lastUpdate value for station %s: %s", self._station_id, last_update_str)
        
        # Parse "2026-02-10 22:39:29 UTC+01:00"
        # Strip timezone suffix for parsing
        if " UTC" in last_update_str:
            last_update_str = last_update_str.split(" UTC")[0]
        
        try:
            # Parse as naive datetime (represents local time from API)
            naive_dt = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
            # Make timezone-aware using Home Assistant's default timezone
            default_tz = dt_util.get_default_time_zone()
            aware_dt = naive_dt.replace(tzinfo=default_tz)
            _LOGGER.debug("Parsed lastUpdate for station %s: %s -> %s", self._station_id, naive_dt, aware_dt)
            return aware_dt
        except (ValueError, TypeError, AttributeError) as exc:
            _LOGGER.warning("Could not parse lastUpdate value '%s' for station %s: %s", last_update_str, self._station_id, exc)
            return None


class DeviceBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for device sensors."""
    
    has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict):
        """Initialize the device sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_record.get("deviceId")
        self._device_name = device_record.get("deviceName", f"Device {self._device_id}")
    
    def _get_device_record(self) -> dict:
        """Get fresh device record from coordinator data."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        device_page_data = stations_devices.get(self._station_id, {})
        device_records = device_page_data.get("data", {}).get("records", [])
        
        # Find device by device_id
        for device_record in device_records:
            if device_record.get("deviceId") == self._device_id:
                return device_record
        return {}
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }


class DeviceDataDtoSensor(DeviceBaseSensor):
    """Base class for device sensors reading from dataDtos array."""
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict,
                 dto_key: str, translation_key: str, device_class, unit: str, state_class):
        """Initialize the dataDtos sensor."""
        super().__init__(coordinator, station_id, station_name, device_record)
        self._dto_key = dto_key
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{self._device_id}_{translation_key}"
        self._attr_translation_key = translation_key
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_native_unit_of_measurement = unit
    
    @property
    def native_value(self):
        """Return the value from dataDtos with matching key."""
        device_record = self._get_device_record()
        data_dtos = device_record.get("dataDtos", [])
        
        # Find the entry with matching key
        for dto in data_dtos:
            if dto.get("key") == self._dto_key:
                return dto.get("value")
        return None


class DeviceWiFiDataSensor(DeviceBaseSensor):
    """Base class for device sensors reading from WiFi data."""
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict,
                 field_name: str, translation_key: str, device_class=None, unit: str = None, state_class=None):
        """Initialize the WiFi data sensor."""
        super().__init__(coordinator, station_id, station_name, device_record)
        self._device_sn = device_record.get("sn") or device_record.get("serialNumber")
        self._field_name = field_name
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{self._device_id}_{translation_key}"
        self._attr_translation_key = translation_key
        if device_class:
            self._attr_device_class = device_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        if state_class:
            self._attr_state_class = state_class
    
    def _get_wifi_data(self) -> dict:
        """Get fresh WiFi data from coordinator for this device."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        device_page_data = stations_devices.get(self._station_id, {})
        wifi_data_dict = device_page_data.get("wifi_data", {})
        return wifi_data_dict.get(self._device_sn, {})
    
    @property
    def native_value(self):
        """Return the WiFi data field value."""
        wifi_data = self._get_wifi_data()
        return wifi_data.get(self._field_name)


class DeviceClusterModeSensor(DeviceBaseSensor):
    """Sensor for device cluster mode (battery clustering configuration)."""
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict):
        """Initialize the cluster mode sensor."""
        super().__init__(coordinator, station_id, station_name, device_record)
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{self._device_id}_cluster_mode"
        self._attr_translation_key = "cluster_mode"
    
    @property
    def native_value(self):
        """Return the cluster mode from connectInfoJson."""
        device_record = self._get_device_record()
        connect_info_json = device_record.get("connectInfoJson", {})
        cluster_mode = connect_info_json.get("clusterMode")
        
        # Handle both string and integer formats
        if cluster_mode is not None:
            return str(cluster_mode)
        return None


class DeviceBatterySOCSensor(DeviceBaseSensor):
    """Sensor for device battery SOC (not in diagnostics category)."""
    
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = None  # Override to not be diagnostic
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict):
        """Initialize the battery SOC sensor."""
        super().__init__(coordinator, station_id, station_name, device_record)
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{self._device_id}_device_battery_soc"
        self._attr_translation_key = "device_battery_soc"
    
    @property
    def native_value(self):
        """Return the battery SOC value from dataDtos, parsing percentage string."""
        device_record = self._get_device_record()
        data_dtos = device_record.get("dataDtos", [])
        
        # Find the entry with key "6002"
        for dto in data_dtos:
            if dto.get("key") == "6002":
                value = dto.get("value")
                if value is None:
                    return None
                
                # Parse percentage string like "4.0%" to float
                if isinstance(value, str):
                    value = value.rstrip('%')
                
                try:
                    return float(value)
                except (ValueError, TypeError):
                    _LOGGER.warning("Could not parse battery SOC value: %s", value)
                    return None
        
        return None


class BatteryLinksBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for sensors reading from battery links data."""
    
    has_entity_name = True
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_id: int):
        """Initialize the battery links base sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
    
    def _get_battery_links_data(self) -> dict:
        """Get fresh battery links data from coordinator."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        device_data = stations_devices.get(self._station_id, {})
        battery_links = device_data.get("battery_links", {})
        return battery_links.get(self._device_id, {})


class BatteryLinkSOCSensor(BatteryLinksBaseSensor):
    """Sensor for battery link SOC percentage."""
    
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    
    def __init__(self, coordinator, station_id: int, station_name: str, parent_device_id: int, parent_device_name: str, link_sn: str):
        """Initialize the battery link SOC sensor."""
        super().__init__(coordinator, station_id, station_name, parent_device_id)
        self._parent_device_name = parent_device_name
        self._link_sn = link_sn
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_battery_link_{link_sn}_soc"
        self._attr_translation_key = "battery_link_soc"
    
    @property
    def device_info(self):
        """Return device information for this battery link."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_battery_link_{self._link_sn}")},
            "name": f"{DISPLAY_NAME_PREFIX} {self._station_name} - {self._parent_device_name} Link {self._link_sn}",
            "manufacturer": DISPLAY_NAME_PREFIX,
            "model": "Battery Link",
            "serial_number": self._link_sn,
            "via_device": (DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}"),
        }
    
    @property
    def native_value(self):
        """Return the battery link SOC value."""
        battery_links_data = self._get_battery_links_data()
        items = battery_links_data.get("data", {}).get("items", [])
        
        # Find the item with matching serial number
        for item in items:
            if item.get("sn") == self._link_sn:
                return item.get("soc")
        
        return None


class BatteryLinkEnergySensor(BatteryLinksBaseSensor):
    """Sensor for battery link energy in kWh."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    
    def __init__(self, coordinator, station_id: int, station_name: str, parent_device_id: int, parent_device_name: str, link_sn: str):
        """Initialize the battery link energy sensor."""
        super().__init__(coordinator, station_id, station_name, parent_device_id)
        self._parent_device_name = parent_device_name
        self._link_sn = link_sn
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_battery_link_{link_sn}_kwh"
        self._attr_translation_key = "battery_link_kwh"
    
    @property
    def device_info(self):
        """Return device information for this battery link."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_battery_link_{self._link_sn}")},
        }
    
    @property
    def native_value(self):
        """Return the battery link energy value in kWh."""
        battery_links_data = self._get_battery_links_data()
        items = battery_links_data.get("data", {}).get("items", [])
        
        # Find the item with matching serial number
        for item in items:
            if item.get("sn") == self._link_sn:
                return item.get("kwh")
        
        return None


class BatteryDeviceEnergySensor(BatteryLinksBaseSensor):
    """Sensor for battery device total energy (socKwh) in kWh."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_id: int, device_name: str):
        """Initialize the battery device energy sensor."""
        super().__init__(coordinator, station_id, station_name, device_id)
        self._device_name = device_name
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_battery_energy"
        self._attr_translation_key = "battery_device_energy"
    
    @property
    def device_info(self):
        """Return device information for this battery device."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def native_value(self):
        """Return the battery device total energy (socKwh) value in kWh."""
        battery_links_data = self._get_battery_links_data()
        return battery_links_data.get("data", {}).get("socKwh")
    
    @property
    def extra_state_attributes(self):
        """Return additional battery data as attributes."""
        battery_links_data = self._get_battery_links_data()
        data = battery_links_data.get("data", {})
        
        attributes = {
            "socKwh": data.get("socKwh"),
            "soc": data.get("soc"),
            "avgSocKwh": data.get("avgSocKwh"),
            "avgSoc": data.get("avgSoc"),
            "batteryPower": data.get("batteryPower"),
            "consumptionPower": data.get("consumptionPower"),
            "solarPower": data.get("solarPower"),
            "pvPower": data.get("pvPower"),
            "offGridPower": data.get("offGridPower"),
            "onlineState": data.get("onlineState"),
        }
        
        # Add charge data
        charge_data = data.get("chargeData", {})
        if charge_data:
            attributes["chargePower1"] = charge_data.get("power1")
            attributes["chargePower2"] = charge_data.get("power2")
            attributes["chargeRemainingTime"] = charge_data.get("remainingTime")
        
        # Add discharge data
        discharge_data = data.get("dischargeData", {})
        if discharge_data:
            attributes["dischargePower1"] = discharge_data.get("power1")
            attributes["dischargePower2"] = discharge_data.get("power2")
            attributes["dischargeRemainingTime"] = discharge_data.get("remainingTime")
        
        # Add PV list if available
        pv_list = data.get("pvList", [])
        if pv_list:
            for idx, pv in enumerate(pv_list, 1):
                attributes[f"pvPower{idx}"] = pv.get("power")
        
        return attributes


class BatteryDeviceSOCSensor(BatteryLinksBaseSensor):
    """Sensor for battery device state of charge (soc) percentage."""
    
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_id: int, device_name: str):
        """Initialize the battery device SOC sensor."""
        super().__init__(coordinator, station_id, station_name, device_id)
        self._device_name = device_name
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_battery_soc_links"
        self._attr_translation_key = "battery_device_soc"
    
    @property
    def device_info(self):
        """Return device information for this battery device."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def native_value(self):
        """Return the battery device state of charge (soc) percentage."""
        battery_links_data = self._get_battery_links_data()
        return battery_links_data.get("data", {}).get("soc")


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Izypower Cloud sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.get("coordinator")
    client = data.get("client")
    
    entities = []
    
    # Get stations data from coordinator
    coordinator_data = coordinator.data or {}
    stations_data = coordinator_data.get("stations", {})
    stations_info_dict = coordinator_data.get("stations_info", {})
    records = stations_data.get("data", {}).get("records", [])
    
    _LOGGER.debug("Found %s station(s) in STATIONS_URL response", len(records))
    
    # Create one device per station record
    for record in records:
        station_id = record.get("stationsId")
        station_name = record.get("stationName", f"Station {station_id}")
        
        if not station_id:
            _LOGGER.warning("Skipping station record without stationsId: %s", record)
            continue
        
        _LOGGER.debug("Creating device for station: %s (ID: %s)", station_name, station_id)
        
        # Create station device sensor
        entities.append(StationDeviceSensor(coordinator, station_id, station_name))
        
        # Fetch station info to get device types mapping
        station_info = stations_info_dict.get(station_id, {})
        
        device_type_mapping = {}
        device_types_enum = station_info.get("deviceTypes", [])
        for device_type_info in device_types_enum:
            type_code = device_type_info.get("value")
            type_name = device_type_info.get("name")
            if type_code and type_name:
                device_type_mapping[type_code] = type_name
                _LOGGER.debug("Mapped device type '%s' to '%s'", type_code, type_name)
        _LOGGER.debug("Complete device type mapping for station %s: %s", station_name, device_type_mapping)
        
        # Create station sensors from extraData
        extra_data = station_info.get("extraData", {})
        if extra_data:
            # Power and SOC sensors
            entities.append(StationDirectFieldSensor(coordinator, station_id, station_name, "power", "production_power", SensorDeviceClass.POWER, "W", SensorStateClass.MEASUREMENT))
            entities.append(StationDirectFieldSensor(coordinator, station_id, station_name, "grid_power", "grid_power", SensorDeviceClass.POWER, "W", SensorStateClass.MEASUREMENT))
            entities.append(StationDirectFieldSensor(coordinator, station_id, station_name, "consumption", "consumption_power", SensorDeviceClass.POWER, "W", SensorStateClass.MEASUREMENT))
            entities.append(StationDirectFieldSensor(coordinator, station_id, station_name, "battery_power", "battery_power", SensorDeviceClass.POWER, "W", SensorStateClass.MEASUREMENT))
            entities.append(StationDirectFieldSensor(coordinator, station_id, station_name, "battery_pv_power", "battery_pv_power", SensorDeviceClass.POWER, "W", SensorStateClass.MEASUREMENT))
            entities.append(StationDirectFieldSensor(coordinator, station_id, station_name, "battery_soc", "battery_soc", SensorDeviceClass.BATTERY, "%", SensorStateClass.MEASUREMENT, None))
            
            # Last update timestamp sensor
            entities.append(StationLastUpdateSensor(coordinator, station_id, station_name))
            
            # Production energy sensors
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "production", "day", "production_day"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "production", "month", "production_month"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "production", "year", "production_year"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "production", "all", "production_total"))
            
            # Grid energy sensors
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "day2", "grid_day_import"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "day1", "grid_day_export"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "month2", "grid_month_import"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "month1", "grid_month_export"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "year2", "grid_year_import"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "year1", "grid_year_export"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "all2", "grid_total_import"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "grid", "all1", "grid_total_export"))
            
            # Consumption energy sensors
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "consumption", "day", "consumption_day"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "consumption", "month", "consumption_month"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "consumption", "year", "consumption_year"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "consumption", "all", "consumption_total"))
            
            # Consumption from PV sensors (calculated)
            entities.append(StationCalculatedEnergySensor(coordinator, station_id, station_name, "day", "consumption_from_pv_day", 
                {"consumption": "day", "battery": "day_out", "grid": "day2"}))
            entities.append(StationCalculatedEnergySensor(coordinator, station_id, station_name, "month", "consumption_from_pv_month", 
                {"consumption": "month", "battery": "month_out", "grid": "month2"}))
            entities.append(StationCalculatedEnergySensor(coordinator, station_id, station_name, "year", "consumption_from_pv_year", 
                {"consumption": "year", "battery": "year_out", "grid": "year2"}))
            entities.append(StationCalculatedEnergySensor(coordinator, station_id, station_name, "total", "consumption_from_pv_total", 
                {"consumption": "all", "battery": "total_out", "grid": "total2"}))
            
            # Battery energy sensors
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "day_in", "battery_day_charge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "day_out", "battery_day_discharge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "month_in", "battery_month_charge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "month_out", "battery_month_discharge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "year_in", "battery_year_charge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "year_out", "battery_year_discharge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "all_in", "battery_total_charge"))
            entities.append(StationEnergySensor(coordinator, station_id, station_name, "battery", "all_out", "battery_total_discharge"))
        
        # Create rate sensors from report data for each time type
        for time_type in ["all", "day", "month", "year"]:
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "cover_rate", "cover_rate"))
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "storage_in_rate", "storage_in_rate"))
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "energy_self_rate", "energy_self_rate"))
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "meter_energy_p_rate", "meter_energy_p_rate"))
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "storage_out_rate", "storage_out_rate"))
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "consumption_rate", "consumption_rate"))
            entities.append(StationRateSensor(coordinator, station_id, station_name, time_type, "meter_energy_n_rate", "meter_energy_n_rate"))
        
        # Query DEVICE_PAGE_URL for this station to get child devices
        try:
            device_page_data = await client.async_get_device_page(
                component_id=station_id,
                device_type="all",
                page=1,
                limit=100
            )
            _LOGGER.debug("DEVICE_PAGE data for station %s (ID: %s): %s", station_name, station_id, device_page_data)
            
            # Parse device records and create child devices
            device_records = device_page_data.get("data", {}).get("records", [])
            _LOGGER.debug("Found %s device(s) for station %s", len(device_records), station_name)
            
            # Create a mapping of serial numbers to device records for PV sensor matching
            device_by_sn = {}
            
            for device_record in device_records:
                device_id = device_record.get("deviceId")
                device_name = device_record.get("deviceName", f"Device {device_id}")
                
                if not device_id:
                    _LOGGER.warning("Skipping device record without deviceId: %s", device_record)
                    continue
                
                _LOGGER.debug(
                    "Creating child device: %s (ID: %s, type: %s) for station %s with mapping: %s", 
                    device_name, 
                    device_id, 
                    device_record.get("deviceType"),
                    station_name,
                    device_type_mapping
                )
                
                # Add online state sensor (also creates the device)
                entities.append(DeviceOnlineStateSensor(coordinator, station_id, station_name, device_record, device_type_mapping))
                
                # Add cluster mode sensor if clusterMode != 0
                cluster_mode_root = device_record.get("clusterMode", 0)
                if cluster_mode_root != 0:
                    entities.append(DeviceClusterModeSensor(coordinator, station_id, station_name, device_record))
                
                # Add battery SOC sensor if key:6002 exists in dataDtos
                data_dtos = device_record.get("dataDtos", [])
                has_battery_soc = any(dto.get("key") == "6002" for dto in data_dtos)
                if has_battery_soc:
                    entities.append(DeviceBatterySOCSensor(coordinator, station_id, station_name, device_record))
                
                # Add WiFi sensors for devices with serial numbers
                serial_number = device_record.get("sn") or device_record.get("serialNumber")
                if serial_number:
                    entities.append(DeviceWiFiDataSensor(coordinator, station_id, station_name, device_record, "rssi", "wifi_signal", SensorDeviceClass.SIGNAL_STRENGTH, "dBm", SensorStateClass.MEASUREMENT))
                    entities.append(DeviceWiFiDataSensor(coordinator, station_id, station_name, device_record, "wifi", "wifi_network"))
                    entities.append(DeviceWiFiDataSensor(coordinator, station_id, station_name, device_record, "ip", "ip_address"))
                    device_by_sn[serial_number] = device_record
                    _LOGGER.debug("Mapped device %s to SN: %s", device_name, serial_number)
            
            # Create battery link sub-devices from coordinator data
            stations_devices = coordinator_data.get("stations_devices", {})
            station_devices_data = stations_devices.get(station_id, {})
            battery_links_dict = station_devices_data.get("battery_links", {})
            _LOGGER.info("Battery links dict for station %s (ID: %s): found %s battery device(s) with links", 
                        station_name, station_id, len(battery_links_dict))
            
            for battery_device_id, battery_links_data in battery_links_dict.items():
                # Find the parent device record
                parent_device_record = next((dr for dr in device_records if dr.get("deviceId") == battery_device_id), None)
                if parent_device_record:
                    parent_device_name = parent_device_record.get("deviceName", f"Device {battery_device_id}")
                    
                    # Add battery device total energy sensor (socKwh) to the parent battery device
                    _LOGGER.info("Creating battery device energy sensor for device %s (ID: %s)", parent_device_name, battery_device_id)
                    entities.append(BatteryDeviceEnergySensor(coordinator, station_id, station_name, battery_device_id, parent_device_name))
                    
                    # Add battery device SOC sensor (soc) to the parent battery device
                    _LOGGER.info("Creating battery device SOC sensor for device %s (ID: %s)", parent_device_name, battery_device_id)
                    entities.append(BatteryDeviceSOCSensor(coordinator, station_id, station_name, battery_device_id, parent_device_name))
                    
                    # Extract items from the battery links data
                    items = battery_links_data.get("data", {}).get("items", [])
                    _LOGGER.info("Found %s battery link(s) for device %s (ID: %s)", len(items), parent_device_name, battery_device_id)
                    
                    # Create sensors for each battery link
                    for item in items:
                        link_sn = item.get("sn")
                        if link_sn:
                            _LOGGER.info("Creating battery link sensors for SN: %s (parent: %s)", link_sn, parent_device_name)
                            entities.append(BatteryLinkSOCSensor(coordinator, station_id, station_name, battery_device_id, parent_device_name, link_sn))
                            entities.append(BatteryLinkEnergySensor(coordinator, station_id, station_name, battery_device_id, parent_device_name, link_sn))
                        else:
                            _LOGGER.warning("Battery link item missing 'sn' field: %s", item)
                else:
                    _LOGGER.warning("Parent device record not found for battery device ID: %s", battery_device_id)
            
            # Query component data for PV sensors at station level
            try:
                current_date = datetime.now().strftime("%Y-%m-%d")
                component_data = await client.async_get_component(
                    component_id=station_id,
                    date=current_date
                )
                _LOGGER.debug("Component data for station %s (ID: %s): %s", station_name, station_id, component_data)
                
                # Parse pvData array
                pv_data_list = component_data.get("pvData", [])
                _LOGGER.debug("Found %s PV data entries for station %s", len(pv_data_list), station_name)
                
                for pv_data in pv_data_list:
                    pv_sn = pv_data.get("sn")
                    pv_name = pv_data.get("pv")
                    
                    if not pv_sn or not pv_name:
                        _LOGGER.debug("Skipping PV data without sn or pv name: %s", pv_data)
                        continue
                    
                    # Find matching device by serial number
                    device_record = device_by_sn.get(pv_sn)
                    if device_record:
                        device_name = device_record.get("deviceName", "Unknown")
                        _LOGGER.debug("Creating PV sensor %s for device %s (SN: %s)", pv_name, device_name, pv_sn)
                        entities.append(DevicePVSensor(coordinator, station_id, station_name, device_record, pv_data))
                    else:
                        _LOGGER.debug("No device found for PV data with SN: %s, pv: %s", pv_sn, pv_name)
            
            except Exception as exc:
                _LOGGER.warning("Failed to fetch component data for station %s (ID: %s): %s", station_name, station_id, exc)
                
        except Exception as exc:
            _LOGGER.warning("Failed to fetch device page for station %s (ID: %s): %s", station_name, station_id, exc)
    
    async_add_entities(entities)


class StationDeviceSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing a power station device."""
    
    def __init__(self, coordinator, station_id: int, station_name: str):
        """Initialize the station device sensor."""
        super().__init__(coordinator)
        self._station_id = station_id
        self._station_name = station_name
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_station_{station_id}_device"
        self._attr_has_entity_name = True
        self._attr_translation_key = "installed_capacity"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "W"
        
        # Get language for device model translation
        language = self.coordinator.hass.config.language or "en"
        self._model_translation = "Centrale" if language == "fr" else "Power Station"
    
    def _get_station_record(self) -> dict:
        """Get the station record from coordinator data."""
        coordinator_data = self.coordinator.data or {}
        stations_data = coordinator_data.get("stations", {})
        records = stations_data.get("data", {}).get("records", [])
        return next((r for r in records if r.get("stationsId") == self._station_id), {})
    
    @property
    def device_info(self):
        """Return device information for this station."""
        station_record = self._get_station_record()
               
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_station_{self._station_id}")},
            "name": f"{DISPLAY_NAME_PREFIX} {self._station_name}",
            "manufacturer": DISPLAY_NAME_PREFIX,
            "model": self._model_translation,
        }
    
    @property
    def native_value(self):
        """Return the installed capacity value."""
        station_record = self._get_station_record()
        return station_record.get("installedCapacity")
    
    @property
    def extra_state_attributes(self):
        """Return additional station attributes."""
        station_record = self._get_station_record()
        
        return {
            "stationsId": station_record.get("stationsId"),
            "stationName": station_record.get("stationName"),
            "address": station_record.get("address")
        }


class DeviceOnlineStateSensor(DeviceBaseSensor):
    """Sensor for device online state."""
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict, device_type_mapping: dict):
        """Initialize the online state sensor."""
        super().__init__(coordinator, station_id, station_name, device_record)
        self._device_type_mapping = device_type_mapping
        self._device_type_code = device_record.get("deviceType")
        self._device_sn = device_record.get("sn") or device_record.get("serialNumber")
        self._device_sw_version = device_record.get("softwareVersion")
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{self._device_id}_online_state"
        self._attr_translation_key = "online_state"
    
    @property
    def device_info(self):
        """Return device information for this online state sensor."""
        device_type_name = self._device_type_mapping.get(self._device_type_code, self._device_type_code)
        
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
            "name": f"{DISPLAY_NAME_PREFIX} {self._station_name} - {self._device_name}",
            "manufacturer": DISPLAY_NAME_PREFIX,
            "model": device_type_name,
            "sw_version": self._device_sw_version,
            "serial_number": self._device_sn,
            "via_device": (DOMAIN, f"{ENTITY_ID_PREFIX}_station_{self._station_id}"),
        }
    
    @property
    def native_value(self):
        """Return the device online state from fresh coordinator data."""
        device_record = self._get_device_record()
        return device_record.get("onlineState")


class DevicePVSensor(DeviceBaseSensor):
    """Sensor for device PV power."""
    
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_has_entity_name = False
    _attr_entity_category = None  # Override to not be diagnostic
    
    def __init__(self, coordinator, station_id: int, station_name: str, device_record: dict, pv_data: dict):
        """Initialize the PV sensor."""
        super().__init__(coordinator, station_id, station_name, device_record)
        self._device_sn = device_record.get("sn") or device_record.get("serialNumber")
        self._pv_name = pv_data.get("pv", "PV").upper()
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{self._device_id}_pv_{self._pv_name}"
        self._attr_name = f"{DISPLAY_NAME_PREFIX} {self._station_name} - {self._device_name} {self._pv_name}"
    
    def _get_pv_data(self) -> dict:
        """Get fresh PV data from coordinator for this sensor's device and PV name."""
        coordinator_data = self.coordinator.data or {}
        stations_component = coordinator_data.get("stations_component", {})
        component_data = stations_component.get(self._station_id, {})
        pv_data_list = component_data.get("pvData", [])
        
        # Find the matching PV data entry by serial number and PV name
        for pv_data in pv_data_list:
            if pv_data.get("sn") == self._device_sn and pv_data.get("pv", "").upper() == self._pv_name:
                return pv_data
        return {}
    
    @property
    def native_value(self):
        """Return the PV power value from fresh coordinator data."""
        pv_data = self._get_pv_data()
        pv_power = pv_data.get("pvPower")
        return 0 if pv_power is None else pv_power



