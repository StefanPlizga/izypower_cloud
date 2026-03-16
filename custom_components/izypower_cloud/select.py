from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENTITY_ID_PREFIX
from .client import ServerUnavailableError

_LOGGER = logging.getLogger(__name__)


def _get_by_device_id(mapping: dict, device_id: int):
    """Return mapping value for device_id supporting int or str keys."""
    if device_id in mapping:
        return mapping.get(device_id)
    return mapping.get(str(device_id))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Izypower Cloud select entities."""
    _LOGGER.info("Setting up select platform for Izypower Cloud")
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    
    entities = []
    
    # Get coordinator data
    coordinator_data = coordinator.data or {}
    stations_data = coordinator_data.get("stations", {}).get("data", {}).get("records", [])
    stations_devices = coordinator_data.get("stations_devices", {})
    
    # Create select entities for each battery device
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
                
                # Battery control mode select - only for master (1000) or standalone (1002) mode
                battery_cmd_for_device = _get_by_device_id(battery_cmd_dict, device_id) if device_id else None
                if device_type == "battery" and device_id and device_sn and battery_cmd_for_device is not None:
                    connect_info_json = device_record.get("connectInfoJson", {})
                    cluster_mode_str = connect_info_json.get("clusterMode")
                    cluster_mode = int(cluster_mode_str) if cluster_mode_str else 0
                    
                    if cluster_mode in (1000, 1002):
                        _LOGGER.debug("Creating control mode select for battery device: %s (ID: %s, cluster_mode: %s)", 
                                     device_name, device_id, cluster_mode)
                        _LOGGER.info("✓✓✓ CREATING BatteryControlModeSelect for device %s (ID: %s, SN: %s)", device_name, device_id, device_sn)
                        entities.append(
                            BatteryControlModeSelect(
                                coordinator,
                                client,
                                station_id,
                                station_name,
                                device_id,
                                device_sn,
                                device_name,
                            )
                        )
                    else:
                        _LOGGER.debug(
                            "Skipping control mode select for battery %s (ID: %s): cluster_mode=%s",
                            device_name,
                            device_id,
                            cluster_mode,
                        )
                elif device_type == "battery":
                    _LOGGER.debug(
                        "Skipping control mode select for battery %s (ID: %s): has_sn=%s has_battery_cmd=%s",
                        device_name,
                        device_id,
                        bool(device_sn),
                        battery_cmd_for_device is not None,
                    )
    
    async_add_entities(entities)


class BatteryControlModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for battery control mode (ctr_mode).
    
    Options: Intelligent (0), Manual (1), Calendar (2)
    """
    
    has_entity_name = True
    _attr_options = ["0", "1", "2"]
    
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
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        
        self._attr_unique_id = f"{device_id}_battery_control_mode"
        self._attr_translation_key = "battery_control_mode"
        
        _LOGGER.info("✓✓✓ BatteryControlModeSelect INITIALIZED for device %s (ID: %s, SN: %s)", device_name, device_id, device_sn)
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def current_option(self) -> str | None:
        """Return the current control mode."""
        _LOGGER.info("✓✓✓ [ControlMode] current_option called for device %s", self._device_id)
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        _LOGGER.info("✓✓✓ [ControlMode] Device %s - station_id in stations_devices: %s", self._device_id, self._station_id in stations_devices)
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = _get_by_device_id(battery_cmd, self._device_id) or {}
            
            _LOGGER.info("✓✓✓ [ControlMode] Device %s battery_cmd keys: %s", self._device_id, list(device_cmd.keys()) if device_cmd else None)
            _LOGGER.info("✓✓✓ [ControlMode] Device %s full device_cmd: %s", self._device_id, device_cmd)
            
            ctr_mode = device_cmd.get("data", {}).get("mode", {}).get("ctr_mode")
            _LOGGER.info("✓✓✓ [ControlMode] Device %s extracted ctr_mode: %s (type: %s)", self._device_id, ctr_mode, type(ctr_mode).__name__ if ctr_mode is not None else None)
            
            return str(ctr_mode) if ctr_mode is not None else None
        
        return None
    
    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        _LOGGER.info("✓✓✓ [ControlMode] available called for device %s", self._device_id)
        
        if not self.coordinator.last_update_success:
            _LOGGER.info("✓✓✓ [ControlMode] Device %s - coordinator.last_update_success is False", self._device_id)
            return False
        
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            # Check if device exists in battery_cmd
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_exists = _get_by_device_id(battery_cmd, self._device_id) is not None
            
            _LOGGER.info("✓✓✓ [ControlMode] Device %s - device_exists in battery_cmd: %s", self._device_id, device_exists)
            
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
                
                _LOGGER.info("✓✓✓ [ControlMode] Device %s - current_cluster_mode: %s", self._device_id, current_cluster_mode)
                
                # Only available for master (1000) or standalone (1002)
                if current_cluster_mode not in (1000, 1002):
                    _LOGGER.info("✓✓✓ [ControlMode] Device %s - UNAVAILABLE due to cluster_mode %s", self._device_id, current_cluster_mode)
                    return False
            
            _LOGGER.info("✓✓✓ [ControlMode] Device %s - returning device_exists: %s", self._device_id, device_exists)
            return device_exists
        
        _LOGGER.info("✓✓✓ [ControlMode] Device %s - station not in stations_devices", self._device_id)
        return False
    
    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            # Convert option string to integer
            ctr_mode_value = int(option)
            
            await self._client.async_set_battery_mode(
                serial_number=self._device_sn,
                ctr_mode=ctr_mode_value,
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
                        stations_devices[self._station_id]["battery_cmd"][str(self._device_id)] = battery_cmd_data
                self.coordinator.async_set_updated_data(self.coordinator.data)
            except Exception as refresh_exc:
                _LOGGER.debug("Failed to refresh battery cmd after mode change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting battery mode: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set battery control mode for %s: %s", self._device_sn, exc)
