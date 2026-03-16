from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfPower

from .const import DOMAIN, ENTITY_ID_PREFIX
from .client import ServerUnavailableError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Izypower Cloud number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    
    entities = []
    
    # Get coordinator data
    coordinator_data = coordinator.data or {}
    stations_data = coordinator_data.get("stations", {}).get("data", {}).get("records", [])
    stations_devices = coordinator_data.get("stations_devices", {})
    
    # Create number entities for each meter device and battery device
    for station_record in stations_data:
        station_id = station_record.get("stationsId")
        station_name = station_record.get("stationsName", "Unknown")
        
        if station_id and station_id in stations_devices:
            device_page_data = stations_devices[station_id]
            device_records = device_page_data.get("data", {}).get("records", [])
            battery_cmd_dict = stations_devices[station_id].get("battery_cmd", {})
            
            for device_record in device_records:
                device_type = device_record.get("deviceType")
                device_id = device_record.get("deviceId")
                device_sn = device_record.get("sn")
                device_name = device_record.get("deviceName", "Unknown")
                
                # Meter injection limit
                if device_type == "meter" and device_id and device_sn:
                    entities.append(
                        MeterInjectionLimitNumber(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                        )
                    )
                
                # Battery min_soc (discharge limit)
                if device_type == "battery" and device_id and device_sn and device_id in battery_cmd_dict:
                    _LOGGER.debug("Creating min_soc number for battery device: %s (ID: %s, SN: %s)", 
                                 device_name, device_id, device_sn)
                    entities.append(
                        BatteryMinSOCNumber(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                        )
                    )
                    
                    # Battery power read-only entities (max charge/discharge)
                    # Only for master (1000) or standalone (1002) mode
                    # Read from connectInfoJson.clusterMode (string from DEVICE_PAGE_URL_TEMPLATE)
                    connect_info_json = device_record.get("connectInfoJson", {})
                    cluster_mode_str = connect_info_json.get("clusterMode")
                    cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                    
                    if cluster_mode in (1000, 1002):
                        _LOGGER.debug("Creating power number entities for battery device: %s (ID: %s, cluster_mode: %s)", 
                                     device_name, device_id, cluster_mode)
                        
                        # Max Charge Power (max_in_power) - for both 1000 and 1002
                        entities.append(
                            BatteryMaxChargePowerNumber(
                                coordinator,
                                client,
                                station_id,
                                station_name,
                                device_id,
                                device_sn,
                                device_name,
                            )
                        )
                        
                        # Max Discharge Power - for both 1000 and 1002
                        entities.append(
                            BatteryMaxDischargePowerNumber(
                                coordinator,
                                client,
                                station_id,
                                station_name,
                                device_id,
                                device_sn,
                                device_name,
                                cluster_mode,
                            )
                        )
    
    async_add_entities(entities)


class MeterInjectionLimitNumber(CoordinatorEntity, NumberEntity):
    """Number entity to control meter injection limit."""
    
    has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 36000
    _attr_native_step = 50
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    
    def __init__(
        self,
        coordinator,
        client,
        station_id: int,
        station_name: str,
        device_id: int,
        device_sn: str,
        device_name: str,
    ):
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        
        self._attr_unique_id = f"{device_id}_injection_limit"
        self._attr_translation_key = "injection_limit"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def native_value(self) -> float | None:
        """Return the current injection limit (as positive value)."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
            device_info = meter_base_info.get(self._device_id, {})
            meter_extra = device_info.get("data", {}).get("meter_extra", {})
            feed_threshold = meter_extra.get("feedThreshold")
            # Convert negative value to positive for display
            return abs(feed_threshold) if feed_threshold is not None else None
        
        return None
    
    @property
    def available(self) -> bool:
        """Return True if entity is available and injection control is enabled."""
        if not self.coordinator.last_update_success:
            return False
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
            if self._device_id not in meter_base_info or meter_base_info.get(self._device_id) is None:
                return False
            
            # Only available when injection control switch is on
            device_info = meter_base_info.get(self._device_id, {})
            meter_extra = device_info.get("data", {}).get("meter_extra", {})
            is_control = meter_extra.get("isControl", False)
            return is_control
        
        return False
    
    async def async_set_native_value(self, value: float) -> None:
        """Set the injection limit (convert positive display value to negative API value)."""
        # Get current isControl state
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        is_control = False  # Default value
        if self._station_id in stations_devices:
            meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
            device_info = meter_base_info.get(self._device_id, {})
            meter_extra = device_info.get("data", {}).get("meter_extra", {})
            is_control = meter_extra.get("isControl", False)
        
        try:
            # Convert positive display value to negative for API (e.g., 300 -> -300)
            feed_threshold_api = -int(abs(value))
            
            await self._client.async_set_meter_control(
                serial_number=self._device_sn,
                is_control=is_control,
                feed_threshold=feed_threshold_api,
            )
            # Fetch only the updated meter base info instead of full coordinator refresh
            try:
                meter_base_info = await self._client.async_get_meter_base_info(device_id=self._device_id)
                # Update coordinator data with new meter info
                if self.coordinator.data:
                    stations_devices = self.coordinator.data.get("stations_devices", {})
                    if self._station_id in stations_devices:
                        if "meter_base_info" not in stations_devices[self._station_id]:
                            stations_devices[self._station_id]["meter_base_info"] = {}
                        stations_devices[self._station_id]["meter_base_info"][self._device_id] = meter_base_info
                # Notify all coordinator entities (switch and number) of the update
                self.coordinator.async_set_updated_data(self.coordinator.data)
            except Exception as refresh_exc:
                _LOGGER.debug("Failed to refresh meter base info after control change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting injection limit: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set injection limit for %s: %s", self._device_sn, exc)


class BatteryMinSOCNumber(CoordinatorEntity, NumberEntity):
    """Number entity to control battery minimum state of charge (discharge limit)."""
    
    has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 5
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"
    
    def __init__(
        self,
        coordinator,
        client,
        station_id: int,
        station_name: str,
        device_id: int,
        device_sn: str,
        device_name: str,
    ):
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        
        self._attr_unique_id = f"{device_id}_battery_min_soc"
        self._attr_translation_key = "battery_min_soc"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def native_value(self) -> float | None:
        """Return the current minimum SOC value."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id, {})
            min_soc = device_cmd.get("data", {}).get("min_soc")
            return min_soc if min_soc is not None else None
        
        return None
    
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            return self._device_id in battery_cmd and battery_cmd.get(self._device_id) is not None
        
        return False
    
    async def async_set_native_value(self, value: float) -> None:
        """Set the minimum SOC (discharge limit)."""
        try:
            # Convert to integer for API
            min_soc_value = int(value)
            
            await self._client.async_set_battery_min_soc(
                serial_number=self._device_sn,
                value=min_soc_value,
            )
            # Fetch only the updated battery cmd data instead of full coordinator refresh
            try:
                battery_cmd_data = await self._client.async_get_battery_cmd(serial_number=self._device_sn)
                # Update coordinator data with new battery cmd info
                if self.coordinator.data:
                    stations_devices = self.coordinator.data.get("stations_devices", {})
                    if self._station_id in stations_devices:
                        if "battery_cmd" not in stations_devices[self._station_id]:
                            stations_devices[self._station_id]["battery_cmd"] = {}
                        stations_devices[self._station_id]["battery_cmd"][self._device_id] = battery_cmd_data
                # Notify all coordinator entities of the update
                self.coordinator.async_set_updated_data(self.coordinator.data)
            except Exception as refresh_exc:
                _LOGGER.debug("Failed to refresh battery cmd after min_soc change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting battery min_soc: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set battery min_soc for %s: %s", self._device_sn, exc)


class BatteryMaxChargePowerNumber(CoordinatorEntity, NumberEntity):
    """Number entity for battery maximum charging power (max_in_power)."""
    
    has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_step = 50
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    
    def __init__(
        self,
        coordinator,
        client,
        station_id: int,
        station_name: str,
        device_id: int,
        device_sn: str,
        device_name: str,
    ):
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        
        self._attr_unique_id = f"{device_id}_battery_max_charge_power"
        self._attr_translation_key = "battery_max_charge_power"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def native_max_value(self) -> float:
        """Return dynamic max value based on cluster mode and battery count."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            # Get cluster mode
            device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
            current_cluster_mode = None
            for device_record in device_records:
                if device_record.get("deviceId") == self._device_id:
                    connect_info_json = device_record.get("connectInfoJson", {})
                    cluster_mode_str = connect_info_json.get("clusterMode")
                    current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                    break
            
            # Get battery count from allBatteries
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id, {})
            all_batteries = device_cmd.get("data", {}).get("allBatteries", [])
            battery_count = len(all_batteries) if all_batteries else 1
            
            # Calculate max based on cluster mode
            if current_cluster_mode == 1000:  # Master - multiply by battery count
                return 2400 * battery_count
            else:  # Standalone (1002) or other - single battery
                return 2400
        
        return 2400  # Default fallback
    
    @property
    def native_value(self) -> float | None:
        """Return the current maximum charging power."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id, {})
            max_in_power = device_cmd.get("data", {}).get("power", {}).get("max_in_power")
            return max_in_power if max_in_power is not None else None
        
        return None
    
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            # Check if device exists in battery_cmd
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_exists = self._device_id in battery_cmd and battery_cmd.get(self._device_id) is not None
            
            if device_exists:
                # Get cluster_mode from connectInfoJson (DEVICE_PAGE_URL_TEMPLATE)
                device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
                current_cluster_mode = None
                for device_record in device_records:
                    if device_record.get("deviceId") == self._device_id:
                        connect_info_json = device_record.get("connectInfoJson", {})
                        cluster_mode_str = connect_info_json.get("clusterMode")
                        current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                        break
                
                # Only available for master (1000) or standalone (1002)
                if current_cluster_mode not in (1000, 1002):
                    return False
            
            return device_exists
        
        return False
    
    async def async_set_native_value(self, value: float) -> None:
        """Set the maximum charging power."""
        try:
            # Get current max_out_power to preserve it
            coordinator_data = self.coordinator.data or {}
            stations_devices = coordinator_data.get("stations_devices", {})
            
            current_max_out_power = 0
            current_cluster_mode = None
            
            if self._station_id in stations_devices:
                # Get max_out_power from battery_cmd
                battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
                device_cmd = battery_cmd.get(self._device_id, {})
                power_data = device_cmd.get("data", {}).get("power", {})
                
                # Get cluster_mode to determine which field to read
                device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
                for device_record in device_records:
                    if device_record.get("deviceId") == self._device_id:
                        connect_info_json = device_record.get("connectInfoJson", {})
                        cluster_mode_str = connect_info_json.get("clusterMode")
                        current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                        break
                
                # Get max_out_power based on cluster mode
                if current_cluster_mode == 1000:
                    current_max_out_power = power_data.get("cluster_max_out_power", 0)
                else:
                    current_max_out_power = power_data.get("max_out_power", 0)
            
            # Determine isCluster based on cluster_mode
            is_cluster = (current_cluster_mode == 1000)
            
            # Convert to integer for API
            max_in_power_value = int(value)
            max_out_power_value = int(current_max_out_power)
            
            await self._client.async_set_battery_power(
                serial_number=self._device_sn,
                max_in_power=max_in_power_value,
                max_out_power=max_out_power_value,
                type="in",
                is_cluster=is_cluster,
            )
            
            # Refresh battery cmd data
            try:
                battery_cmd_data = await self._client.async_get_battery_cmd(serial_number=self._device_sn)
                if self.coordinator.data:
                    stations_devices = self.coordinator.data.get("stations_devices", {})
                    if self._station_id in stations_devices:
                        if "battery_cmd" not in stations_devices[self._station_id]:
                            stations_devices[self._station_id]["battery_cmd"] = {}
                        stations_devices[self._station_id]["battery_cmd"][self._device_id] = battery_cmd_data
                self.coordinator.async_set_updated_data(self.coordinator.data)
            except Exception as refresh_exc:
                _LOGGER.debug("Failed to refresh battery cmd after power change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting battery power: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set battery max charge power for %s: %s", self._device_sn, exc)


class BatteryMaxDischargePowerNumber(CoordinatorEntity, NumberEntity):
    """Number entity for battery maximum discharging power.
    
    For master mode (1000): reads cluster_max_out_power
    For standalone mode (1002): reads max_out_power
    """
    
    has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_step = 50
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    
    def __init__(
        self,
        coordinator,
        client,
        station_id: int,
        station_name: str,
        device_id: int,
        device_sn: str,
        device_name: str,
        cluster_mode: int,
    ):
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        self._cluster_mode = cluster_mode
        
        self._attr_unique_id = f"{device_id}_battery_max_discharge_power"
        self._attr_translation_key = "battery_max_discharge_power"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def native_max_value(self) -> float:
        """Return dynamic max value based on cluster mode and battery count."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            # Get cluster mode
            device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
            current_cluster_mode = None
            for device_record in device_records:
                if device_record.get("deviceId") == self._device_id:
                    connect_info_json = device_record.get("connectInfoJson", {})
                    cluster_mode_str = connect_info_json.get("clusterMode")
                    current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                    break
            
            # Get battery count from allBatteries
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id, {})
            all_batteries = device_cmd.get("data", {}).get("allBatteries", [])
            battery_count = len(all_batteries) if all_batteries else 1
            
            # Calculate max based on cluster mode
            if current_cluster_mode == 1000:  # Master - multiply by battery count
                return 2400 * battery_count
            else:  # Standalone (1002) or other - single battery
                return 2400
        
        return 2400  # Default fallback
    
    @property
    def native_value(self) -> float | None:
        """Return the current maximum discharging power."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id, {})
            power_data = device_cmd.get("data", {}).get("power", {})
            
            # Get current cluster_mode from connectInfoJson (DEVICE_PAGE_URL_TEMPLATE)
            device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
            current_cluster_mode = None
            for device_record in device_records:
                if device_record.get("deviceId") == self._device_id:
                    connect_info_json = device_record.get("connectInfoJson", {})
                    cluster_mode_str = connect_info_json.get("clusterMode")
                    current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                    break
            
            # Use cluster_max_out_power for master (1000), max_out_power for standalone (1002)
            if current_cluster_mode == 1000:
                max_out_power = power_data.get("cluster_max_out_power")
            else:  # 1002 or fallback
                max_out_power = power_data.get("max_out_power")
            
            return max_out_power if max_out_power is not None else None
        
        return None
    
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            # Check if device exists in battery_cmd
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_exists = self._device_id in battery_cmd and battery_cmd.get(self._device_id) is not None
            
            if device_exists:
                # Get cluster_mode from connectInfoJson (DEVICE_PAGE_URL_TEMPLATE)
                device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
                current_cluster_mode = None
                for device_record in device_records:
                    if device_record.get("deviceId") == self._device_id:
                        connect_info_json = device_record.get("connectInfoJson", {})
                        cluster_mode_str = connect_info_json.get("clusterMode")
                        current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                        break
                
                # Only available for master (1000) or standalone (1002)
                if current_cluster_mode not in (1000, 1002):
                    return False
            
            return device_exists
        
        return False
    
    async def async_set_native_value(self, value: float) -> None:
        """Set the maximum discharging power."""
        try:
            # Get current max_in_power to preserve it
            coordinator_data = self.coordinator.data or {}
            stations_devices = coordinator_data.get("stations_devices", {})
            
            current_max_in_power = 0
            current_cluster_mode = None
            
            if self._station_id in stations_devices:
                # Get max_in_power from battery_cmd
                battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
                device_cmd = battery_cmd.get(self._device_id, {})
                power_data = device_cmd.get("data", {}).get("power", {})
                current_max_in_power = power_data.get("max_in_power", 0)
                
                # Get cluster_mode
                device_records = stations_devices[self._station_id].get("data", {}).get("records", [])
                for device_record in device_records:
                    if device_record.get("deviceId") == self._device_id:
                        connect_info_json = device_record.get("connectInfoJson", {})
                        cluster_mode_str = connect_info_json.get("clusterMode")
                        current_cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                        break
            
            # Determine isCluster based on cluster_mode
            is_cluster = (current_cluster_mode == 1000)
            
            # Convert to integer for API
            max_in_power_value = int(current_max_in_power)
            max_out_power_value = int(value)
            
            await self._client.async_set_battery_power(
                serial_number=self._device_sn,
                max_in_power=max_in_power_value,
                max_out_power=max_out_power_value,
                type="out",
                is_cluster=is_cluster,
            )
            
            # Refresh battery cmd data
            try:
                battery_cmd_data = await self._client.async_get_battery_cmd(serial_number=self._device_sn)
                if self.coordinator.data:
                    stations_devices = self.coordinator.data.get("stations_devices", {})
                    if self._station_id in stations_devices:
                        if "battery_cmd" not in stations_devices[self._station_id]:
                            stations_devices[self._station_id]["battery_cmd"] = {}
                        stations_devices[self._station_id]["battery_cmd"][self._device_id] = battery_cmd_data
                self.coordinator.async_set_updated_data(self.coordinator.data)
            except Exception as refresh_exc:
                _LOGGER.debug("Failed to refresh battery cmd after power change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting battery power: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set battery max discharge power for %s: %s", self._device_sn, exc)

