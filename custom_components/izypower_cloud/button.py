from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Set up Izypower Cloud button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    
    entities = []
    
    # Get coordinator data
    coordinator_data = coordinator.data or {}
    stations_data = coordinator_data.get("stations", {}).get("data", {}).get("records", [])
    stations_devices = coordinator_data.get("stations_devices", {})
    
    # Create button entities for each battery device
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
                
                # Create buttons for all battery devices with a serial number
                if device_type == "battery" and device_id and device_sn:
                    _LOGGER.debug("Creating LED buttons for battery device: %s (ID: %s, SN: %s)", 
                                 device_name, device_id, device_sn)
                    entities.append(
                        BatteryLEDButton(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                            led_value=1,
                            translation_key="battery_led_on",
                        )
                    )
                    entities.append(
                        BatteryLEDButton(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                            led_value=0,
                            translation_key="battery_led_off",
                        )
                    )
    
    async_add_entities(entities)


class BatteryLEDButton(CoordinatorEntity, ButtonEntity):
    """Button entity to control battery LED lights."""
    
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
        led_value: int,
        translation_key: str,
    ):
        """Initialize the button entity."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        self._led_value = led_value
        
        self._attr_unique_id = f"{device_id}_{translation_key}"
        self._attr_translation_key = translation_key
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success
    
    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self._client.async_set_battery_led(
                serial_number=self._device_sn,
                value=self._led_value,
            )
            _LOGGER.info(
                "Battery LED %s for %s (ID: %s, SN: %s)",
                "turned on" if self._led_value == 1 else "turned off",
                self._device_name,
                self._device_id,
                self._device_sn
            )
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting battery LED: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set battery LED for %s: %s", self._device_sn, exc)
