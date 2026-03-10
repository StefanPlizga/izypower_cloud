from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENTITY_ID_PREFIX
from .client import ServerUnavailableError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Izypower Cloud switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    
    entities = []
    
    # Get coordinator data
    coordinator_data = coordinator.data or {}
    stations_data = coordinator_data.get("stations", {}).get("data", {}).get("records", [])
    stations_devices = coordinator_data.get("stations_devices", {})
    
    # Create a switch for each meter device
    for station_record in stations_data:
        station_id = station_record.get("stationsId")
        station_name = station_record.get("stationsName", "Unknown")
        
        if station_id and station_id in stations_devices:
            device_page_data = stations_devices[station_id]
            device_records = device_page_data.get("data", {}).get("records", [])
            
            for device_record in device_records:
                device_type = device_record.get("deviceType")
                device_id = device_record.get("deviceId")
                device_sn = device_record.get("sn")
                device_name = device_record.get("deviceName", "Unknown")
                
                if device_type == "meter" and device_id and device_sn:
                    entities.append(
                        MeterInjectionControlSwitch(
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


class MeterInjectionControlSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to control meter injection blocking."""
    
    has_entity_name = True
    
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
        """Initialize the switch."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        
        self._attr_unique_id = f"{device_id}_injection_control"
        self._attr_translation_key = "injection_control"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def is_on(self) -> bool | None:
        """Return True if injection control is enabled."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
            device_info = meter_base_info.get(self._device_id, {})
            meter_extra = device_info.get("data", {}).get("meter_extra", {})
            return meter_extra.get("isControl", False)
        
        return None
    
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
            return self._device_id in meter_base_info and meter_base_info.get(self._device_id) is not None
        
        return False
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on injection control."""
        # Get current feed threshold from the number entity state
        feed_threshold = -300  # Default value
        
        # Try to get the value from the number entity's current state
        number_entity_id = f"number.{self._device_name.lower().replace(' ', '_')}_injection_limit"
        number_state = self.hass.states.get(number_entity_id)
        
        if number_state and number_state.state not in ("unknown", "unavailable"):
            try:
                # Number entity shows positive values, convert to negative for API
                feed_threshold = -int(float(number_state.state))
            except (ValueError, TypeError):
                _LOGGER.debug("Could not parse number entity state, using default: %s", number_state.state)
        
        # Fallback to coordinator data if number entity not found
        if feed_threshold == -300:
            coordinator_data = self.coordinator.data or {}
            stations_devices = coordinator_data.get("stations_devices", {})
            if self._station_id in stations_devices:
                meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
                device_info = meter_base_info.get(self._device_id, {})
                meter_extra = device_info.get("data", {}).get("meter_extra", {})
                feed_threshold = meter_extra.get("feedThreshold", -300)
        
        _LOGGER.debug("Turning on injection control for %s with feedThreshold=%s", self._device_sn, feed_threshold)
        
        try:
            await self._client.async_set_meter_control(
                serial_number=self._device_sn,
                is_control=True,
                feed_threshold=feed_threshold,
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
            _LOGGER.info("Server temporarily unavailable when setting meter control: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to turn on injection control for %s: %s", self._device_sn, exc)
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off injection control."""
        # Get current feed threshold from the number entity state
        feed_threshold = -300  # Default value
        
        # Try to get the value from the number entity's current state
        number_entity_id = f"number.{self._device_name.lower().replace(' ', '_')}_injection_limit"
        number_state = self.hass.states.get(number_entity_id)
        
        if number_state and number_state.state not in ("unknown", "unavailable"):
            try:
                # Number entity shows positive values, convert to negative for API
                feed_threshold = -int(float(number_state.state))
            except (ValueError, TypeError):
                _LOGGER.debug("Could not parse number entity state, using default: %s", number_state.state)
        
        # Fallback to coordinator data if number entity not found
        if feed_threshold == -300:
            coordinator_data = self.coordinator.data or {}
            stations_devices = coordinator_data.get("stations_devices", {})
            if self._station_id in stations_devices:
                meter_base_info = stations_devices[self._station_id].get("meter_base_info", {})
                device_info = meter_base_info.get(self._device_id, {})
                meter_extra = device_info.get("data", {}).get("meter_extra", {})
                feed_threshold = meter_extra.get("feedThreshold", -300)
        
        _LOGGER.debug("Turning off injection control for %s with feedThreshold=%s", self._device_sn, feed_threshold)
        
        try:
            await self._client.async_set_meter_control(
                serial_number=self._device_sn,
                is_control=False,
                feed_threshold=feed_threshold,
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
            _LOGGER.info("Server temporarily unavailable when setting meter control: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to turn off injection control for %s: %s", self._device_sn, exc)
