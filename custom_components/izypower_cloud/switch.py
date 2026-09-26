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


def _get_cluster_mode(device_record: dict) -> int:
    """Return the numeric cluster mode from a battery device record."""
    connect_info_json = device_record.get("connectInfoJson", {})
    cluster_mode = connect_info_json.get("clusterMode", device_record.get("clusterMode", 0))
    try:
        return int(cluster_mode)
    except (TypeError, ValueError):
        return 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Isypower Cloud switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]
    live_mode = data["live_mode"]
    
    entities = []
    priority_stations: set[int] = set()
    
    # Get coordinator data
    coordinator_data = coordinator.data or {}
    stations_data = coordinator_data.get("stations", {}).get("data", {}).get("records", [])
    stations_devices = coordinator_data.get("stations_devices", {})

    # Live-mode switches are intentionally kept in the code for a future stage,
    # but they are temporarily disabled here to avoid exposing them in Home Assistant.
    # for station_record in stations_data:
    #     station_id = station_record.get("stationsId")
    #     station_name = station_record.get("stationName", "Unknown")
    #     if station_id:
    #         entities.append(StationLiveModeSwitch(coordinator, live_mode, station_id, station_name))

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
                device_sn = device_record.get("sn") or device_record.get("serialNumber")
                device_name = device_record.get("deviceName", "Unknown")
                
                if device_type == "meter" and device_id and device_sn:
                    # Meter live-mode switch intentionally disabled for now.
                    # entities.append(
                    #     MeterLiveModeSwitch(
                    #         coordinator,
                    #         live_mode,
                    #         station_id,
                    #         station_name,
                    #         device_id,
                    #         device_name,
                    #         device_sn,
                    #     )
                    # )
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
                elif device_type == "evse" and device_id and device_sn:
                    if station_id not in priority_stations:
                        priority_stations.add(station_id)
                        entities.append(
                            EVSEPrioritySwitch(
                                coordinator,
                                client,
                                station_id,
                                station_name,
                            )
                        )
                    # EVSE live-mode switch intentionally disabled for now.
                    # entities.append(
                    #     EVSELiveModeSwitch(
                    #         coordinator,
                    #         live_mode,
                    #         station_id,
                    #         station_name,
                    #         device_id,
                    #         device_name,
                    #         device_sn,
                    #     )
                    # )
                    entities.append(
                        EVSEStartedSwitch(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                        )
                    )
                    entities.append(
                        EVSEIntelligentOptionSwitch(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                            option_key="enableGrid",
                            translation_key="evse_enable_grid",
                        )
                    )
                    entities.append(
                        EVSEIntelligentOptionSwitch(
                            coordinator,
                            client,
                            station_id,
                            station_name,
                            device_id,
                            device_sn,
                            device_name,
                            option_key="enableBattery",
                            translation_key="evse_enable_battery",
                        )
                    )
                elif device_type == "battery" and device_id and device_sn:
                    cluster_mode = _get_cluster_mode(device_record)
                    if cluster_mode in (1000, 1002):
                        batteries = [(device_id, device_sn)]
                        if cluster_mode == 1000:
                            batteries.extend(
                                (slave.get("deviceId"), slave.get("sn") or slave.get("serialNumber"))
                                for slave in device_records
                                if slave.get("deviceType") == "battery"
                                and slave.get("deviceId")
                                and (slave.get("sn") or slave.get("serialNumber"))
                                and _get_cluster_mode(slave) == 1001
                            )
                        # Battery live-mode switch intentionally disabled for now.
                        # entities.append(
                        #     StationBatteryLiveModeSwitch(
                        #         coordinator,
                        #         live_mode,
                        #         station_id,
                        #         station_name,
                        #         device_id,
                        #         device_name,
                        #         batteries,
                        #     )
                        # )

                    # Get battery_cmd data for this device
                    battery_cmd_dict = stations_devices[station_id].get("battery_cmd", {})
                    battery_cmd_for_device = battery_cmd_dict.get(device_id) or battery_cmd_dict.get(str(device_id))
                    
                    if battery_cmd_for_device is not None:
                        entities.append(
                            BatteryOffgridSwitch(
                                coordinator,
                                client,
                                station_id,
                                station_name,
                                device_id,
                                device_sn,
                                device_name,
                            )
                        )
                        entities.append(
                            BatteryCalibrationSwitch(
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


class StationLiveModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for station live mode."""

    has_entity_name = True

    def __init__(self, coordinator, live_mode, station_id: int, station_name: str):
        super().__init__(coordinator)
        self._live_mode = live_mode
        self._station_id = station_id
        self._station_name = station_name
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_station_{station_id}_live_mode"
        self._attr_translation_key = "live_mode"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_station_{self._station_id}")}}

    @property
    def is_on(self) -> bool:
        return self._live_mode.is_active(self._station_id)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success or self.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        try:
            await self._live_mode.activate(self._station_id)
            _LOGGER.debug("Enabled live mode for station %s", self._station_id)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when enabling live mode: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to enable live mode for station %s: %s", self._station_id, exc)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._live_mode.deactivate(self._station_id)
        _LOGGER.debug("Disabled live mode for station %s", self._station_id)


class MeterLiveModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for smart-meter live mode."""

    has_entity_name = True

    def __init__(self, coordinator, live_mode, station_id: int, station_name: str,
                 device_id: int, device_name: str, serial_number: str):
        super().__init__(coordinator)
        self._live_mode = live_mode
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_name = device_name
        self._serial_number = serial_number
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_meter_live_mode"
        self._attr_translation_key = "live_mode"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")}}

    @property
    def is_on(self) -> bool:
        return self._live_mode.is_meter_active(self._station_id, self._device_id)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success or self.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        try:
            await self._live_mode.activate_meter(self._station_id, self._device_id, self._serial_number)
            _LOGGER.debug("Enabled meter live mode for station %s, meter %s", self._station_id, self._device_id)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when enabling meter live mode: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to enable meter live mode for meter %s: %s", self._device_id, exc)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._live_mode.deactivate_meter(self._station_id, self._device_id)
        _LOGGER.debug("Disabled meter live mode for station %s, meter %s", self._station_id, self._device_id)


class EVSELiveModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for EVSE live mode."""

    has_entity_name = True

    def __init__(self, coordinator, live_mode, station_id: int, station_name: str,
                 device_id: int, device_name: str, serial_number: str):
        super().__init__(coordinator)
        self._live_mode = live_mode
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_name = device_name
        self._serial_number = serial_number
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_evse_live_mode"
        self._attr_translation_key = "live_mode"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")}}

    @property
    def is_on(self) -> bool:
        return self._live_mode.is_evse_active(self._station_id, self._device_id)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success or self.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        try:
            await self._live_mode.activate_evse(self._station_id, self._device_id, self._serial_number)
            _LOGGER.debug("Enabled EVSE live mode for station %s, device %s", self._station_id, self._device_id)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when enabling EVSE live mode: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to enable EVSE live mode for device %s: %s", self._device_id, exc)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._live_mode.deactivate_evse(self._station_id, self._device_id)
        _LOGGER.debug("Disabled EVSE live mode for station %s, device %s", self._station_id, self._device_id)


class StationBatteryLiveModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for battery-link live mode."""

    has_entity_name = True

    def __init__(self, coordinator, live_mode, station_id: int, station_name: str,
                 device_id: int, device_name: str, batteries: list[tuple[int, str]]):
        super().__init__(coordinator)
        self._live_mode = live_mode
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_name = device_name
        self._batteries = batteries
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_battery_live_mode"
        self._attr_translation_key = "live_mode"

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")}}

    @property
    def is_on(self) -> bool:
        return self._live_mode.is_battery_active(self._station_id, self._device_id)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success or self.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        try:
            await self._live_mode.activate_battery(self._station_id, self._device_id, self._batteries)
            _LOGGER.debug(
                "Enabled battery live mode for station %s, battery %s (%s batteries)",
                self._station_id,
                self._device_id,
                len(self._batteries),
            )
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when enabling battery live mode: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to enable battery live mode for battery %s: %s", self._device_id, exc)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._live_mode.deactivate_battery(self._station_id, self._device_id)
        _LOGGER.debug("Disabled battery live mode for station %s, battery %s", self._station_id, self._device_id)


class EVSEPrioritySwitch(CoordinatorEntity, SwitchEntity):
    """Switch for the station-level EVSE charging priority."""

    has_entity_name = True

    def __init__(
        self,
        coordinator,
        client,
        station_id: int,
        station_name: str,
    ):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_station_{station_id}_evse_priority"
        self._attr_translation_key = "evse_priority"

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_station_{self._station_id}")},
        }

    def _get_priority_data(self) -> dict:
        coordinator_data = self.coordinator.data or {}
        station_devices = coordinator_data.get("stations_devices", {}).get(self._station_id, {})
        return station_devices.get("evse_priority", {}) or {}

    @property
    def is_on(self) -> bool | None:
        """Return True when the charging station has priority."""
        value = self._get_priority_data().get("value")
        return bool(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        return "value" in self._get_priority_data()

    async def _async_set_priority(self, value: bool) -> None:
        try:
            await self._client.async_set_evse_priority(
                station_id=self._station_id,
                value=value,
            )
            priority_data = await self._client.async_get_evse_priority(station_id=self._station_id)
            data = dict(self.coordinator.data or {})
            stations_devices = dict(data.get("stations_devices", {}))
            station_devices = dict(stations_devices.get(self._station_id, {}))
            station_devices["evse_priority"] = priority_data
            stations_devices[self._station_id] = station_devices
            data["stations_devices"] = stations_devices
            self.coordinator.async_set_updated_data(data)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting EVSE priority: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set EVSE priority for station %s: %s", self._station_id, exc)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable charging station priority."""
        await self._async_set_priority(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable charging station priority."""
        await self._async_set_priority(False)


class EVSEStartedSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to start or stop the EVSE."""

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
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_evse_started"
        self._attr_translation_key = "evse_started"

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }

    def _get_command_data(self) -> dict:
        coordinator_data = self.coordinator.data or {}
        station_devices = coordinator_data.get("stations_devices", {}).get(self._station_id, {})
        evse_cmd = station_devices.get("evse_cmd", {})
        device_cmd = evse_cmd.get(self._device_id) or evse_cmd.get(str(self._device_id)) or {}
        return device_cmd.get("data", {})

    @property
    def is_on(self) -> bool | None:
        """Return True when the EVSE is started."""
        value = self._get_command_data().get("open")
        return bool(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        return "open" in self._get_command_data()

    async def _async_set_state(self, value: bool) -> None:
        try:
            await self._client.async_set_evse_state(
                serial_number=self._device_sn,
                value=value,
            )
            command_data = await self._client.async_get_battery_cmd(serial_number=self._device_sn)
            data = dict(self.coordinator.data or {})
            stations_devices = dict(data.get("stations_devices", {}))
            station_devices = dict(stations_devices.get(self._station_id, {}))
            evse_cmd = dict(station_devices.get("evse_cmd", {}))
            evse_cmd[self._device_id] = command_data
            station_devices["evse_cmd"] = evse_cmd
            stations_devices[self._station_id] = station_devices
            data["stations_devices"] = stations_devices
            self.coordinator.async_set_updated_data(data)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting EVSE state: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set EVSE state for %s: %s", self._device_sn, exc)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the EVSE."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the EVSE."""
        await self._async_set_state(False)


class EVSEIntelligentOptionSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for an EVSE intelligent-mode option (enableGrid / enableBattery)."""

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
        option_key: str,
        translation_key: str,
    ):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._client = client
        self._station_id = station_id
        self._station_name = station_name
        self._device_id = device_id
        self._device_sn = device_sn
        self._device_name = device_name
        self._option_key = option_key
        self._attr_unique_id = f"{ENTITY_ID_PREFIX}_device_{device_id}_{translation_key}"
        self._attr_translation_key = translation_key

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }

    def _get_intelligent_value(self) -> dict:
        coordinator_data = self.coordinator.data or {}
        station_devices = coordinator_data.get("stations_devices", {}).get(self._station_id, {})
        intelligent = station_devices.get("evse_intelligent", {})
        return intelligent.get(self._device_id) or intelligent.get(str(self._device_id)) or {}

    @property
    def is_on(self) -> bool | None:
        """Return True when the intelligent option is enabled."""
        value = self._get_intelligent_value().get(self._option_key)
        return bool(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        return self._option_key in self._get_intelligent_value()

    async def _async_set_option(self, value: bool) -> None:
        current = self._get_intelligent_value()
        enable_grid = bool(current.get("enableGrid", False))
        enable_battery = bool(current.get("enableBattery", False))

        if self._option_key == "enableGrid":
            enable_grid = value
        else:
            enable_battery = value

        try:
            await self._client.async_set_evse_intelligent_value(
                serial_number=self._device_sn,
                enable_grid=enable_grid,
                enable_battery=enable_battery,
            )
            intelligent_value = await self._client.async_get_evse_intelligent_value(serial_number=self._device_sn)
            data = dict(self.coordinator.data or {})
            stations_devices = dict(data.get("stations_devices", {}))
            station_devices = dict(stations_devices.get(self._station_id, {}))
            evse_intelligent = dict(station_devices.get("evse_intelligent", {}))
            evse_intelligent[self._device_id] = intelligent_value
            station_devices["evse_intelligent"] = evse_intelligent
            stations_devices[self._station_id] = station_devices
            data["stations_devices"] = stations_devices
            self.coordinator.async_set_updated_data(data)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when setting EVSE intelligent value: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to set EVSE %s for %s: %s", self._option_key, self._device_sn, exc)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the intelligent option."""
        await self._async_set_option(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the intelligent option."""
        await self._async_set_option(False)


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


class BatteryOffgridSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to control battery backup outlet (off-grid mode)."""
    
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
        
        self._attr_unique_id = f"{device_id}_battery_offgrid"
        self._attr_translation_key = "battery_offgrid"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def is_on(self) -> bool | None:
        """Return True if backup outlet is enabled."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id) or battery_cmd.get(str(self._device_id))
            if device_cmd:
                return device_cmd.get("data", {}).get("offGrid", {}).get("open", False)
        
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
            device_cmd = battery_cmd.get(self._device_id) or battery_cmd.get(str(self._device_id))
            return device_cmd is not None
        
        return False
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on backup outlet."""
        _LOGGER.debug("Turning on backup outlet for %s", self._device_sn)
        
        try:
            await self._client.async_toggle_offgrid(
                serial_number=self._device_sn,
                value=True,
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
                _LOGGER.debug("Failed to refresh battery cmd after backup outlet change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when toggling backup outlet: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to turn on backup outlet for %s: %s", self._device_sn, exc)
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off backup outlet."""
        _LOGGER.debug("Turning off backup outlet for %s", self._device_sn)
        
        try:
            await self._client.async_toggle_offgrid(
                serial_number=self._device_sn,
                value=False,
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
                _LOGGER.debug("Failed to refresh battery cmd after backup outlet change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when toggling backup outlet: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to turn off backup outlet for %s: %s", self._device_sn, exc)


class BatteryCalibrationSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to control battery calibration (full charge)."""
    
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
        
        self._attr_unique_id = f"{device_id}_battery_calibration"
        self._attr_translation_key = "battery_calibration"
    
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, f"{ENTITY_ID_PREFIX}_device_{self._device_id}")},
        }
    
    @property
    def is_on(self) -> bool | None:
        """Return True if calibration is enabled."""
        coordinator_data = self.coordinator.data or {}
        stations_devices = coordinator_data.get("stations_devices", {})
        
        if self._station_id in stations_devices:
            battery_cmd = stations_devices[self._station_id].get("battery_cmd", {})
            device_cmd = battery_cmd.get(self._device_id) or battery_cmd.get(str(self._device_id))
            if device_cmd:
                return device_cmd.get("data", {}).get("fullCharge", {}).get("open", False)
        
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
            device_cmd = battery_cmd.get(self._device_id) or battery_cmd.get(str(self._device_id))
            return device_cmd is not None
        
        return False
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on calibration."""
        _LOGGER.debug("Turning on calibration for %s", self._device_sn)
        
        try:
            await self._client.async_toggle_fullcharge_days(
                serial_number=self._device_sn,
                value=True,
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
                _LOGGER.debug("Failed to refresh battery cmd after calibration change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when toggling calibration: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to turn on calibration for %s: %s", self._device_sn, exc)
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off calibration."""
        _LOGGER.debug("Turning off calibration for %s", self._device_sn)
        
        try:
            await self._client.async_toggle_fullcharge_days(
                serial_number=self._device_sn,
                value=False,
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
                _LOGGER.debug("Failed to refresh battery cmd after calibration change: %s", refresh_exc)
        except ServerUnavailableError as exc:
            _LOGGER.info("Server temporarily unavailable when toggling calibration: %s", exc)
        except Exception as exc:
            _LOGGER.error("Failed to turn off calibration for %s: %s", self._device_sn, exc)


