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
