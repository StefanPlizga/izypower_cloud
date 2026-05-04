# Izypower Cloud Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-green.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/StefanPlizga/izypower_cloud.svg?style=for-the-badge)](https://github.com/StefanPlizga/izypower_cloud/releases)
[![GitHub pre-release](https://img.shields.io/github/v/release/StefanPlizga/izypower_cloud?include_prereleases&label=Beta&style=for-the-badge)](https://github.com/StefanPlizga/izypower_cloud/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Maintenance](https://img.shields.io/maintenance/yes/2026.svg?style=for-the-badge)](https://github.com/StefanPlizga/izypower_cloud)

This custom integration automatically discovers all Izypower Cloud power stations and provides comprehensive monitoring of your solar installation.

**You need an Izypower account to use this integration. Only devices from the Izypower range by Materfrance are supported by this integration.**

> **Important Note**: This is a community integration and is not developped by Materfrance.

## Acknowledgments

Thanks to Khirale, MarcoCMG, Wellgo and Zyos67 for testing and feedback.

## Installation

### Via HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance
2. Search for "Izypower Cloud" in HACS and click "Download"
3. Restart Home Assistant
4. Click the button below to add the integration:

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=izypower_cloud)

   Or manually:
   - Go to Settings > Devices & Services > Add Integration
   - Search for "Izypower Cloud" and follow the configuration steps

### Manual Installation

1. Download the latest release from [GitHub](https://github.com/StefanPlizga/izypower_cloud)
2. Extract the contents and copy the `custom_components/izypower_cloud` folder to your Home Assistant's `custom_components` directory
3. If the `custom_components` folder doesn't exist, create it in the root of your Home Assistant configuration
4. Restart Home Assistant
5. Go to Settings > Devices & Services > Add Integration
6. Search for "Izypower Cloud" and follow the configuration steps

## Configuration

- Add the integration via the Home Assistant UI
- Enter your Izypower Cloud `username` and `password`
- Optional: Set `refresh_period` in minutes (default: 3 minutes)
- After setup, you can modify `refresh_period` from the integration Options menu

> **Note**: The default refresh period is set to 3 minutes because the data comes from the Izypower Cloud and is updated in the cloud every 3 minutes. Therefore, there is no need to refresh more frequently. The data is not real-time, as in the Izypower Cloud application.

## Energy Dashboard Configuration

The statistics to configure in the Energy dashboard are (the names of statistics are in French):

- **Grid connections**: look for the statistic ending with `Reseau Import Stats` for import and `Reseau Export Stats` for export. The power sensor remains unchanged: `Grid Power`.
- **Solar panels**: look for the statistic ending with `Production Stats`. The power sensor remains unchanged: `PV Production Power`.
- **Home battery storage**: look for the statistic ending with `Batterie Charge Stats` for charge and `Batterie Decharge Stats` for discharge. The power sensor remains unchanged: `Battery Power` (in inverted mode).

The created statistics use energy sensor data to preserve the Izypower ecosystem consumption/production history in the Energy dashboard. This allows previous sensors to be removed from the Energy dashboard.

After updating the integration, wait a few minutes for history to be copied from energy sensors into statistics (for users who already have existing integration history). Statistics are then updated every hour, around 10 minutes after the start of each hour.

## Features

### Automatic Discovery
- All power stations in your Izypower Cloud account are automatically discovered
- Each station is created as a device with all associated sensors
- Sub-devices are created for inverters and other equipment

### Station Sensors (Per Power Station)

**Power Sensors** (W):
- PV Production Power
- Grid Power
- Consumption Power
- Battery Power
- Battery PV Power

**Station Battery Sensors**:
- Battery State of Charge (%)
- Last Update (timestamp)
- Station Upgrade (Available/None) - Indicates if at least one device in the station has an available update

**Energy Sensors** (kWh):
- Production: Day, Month, Year, Total
- Grid Import: Day, Month, Year, Total
- Grid Export: Day, Month, Year, Total
- Consumption: Day, Month, Year, Total
- Consumption from PV: Day, Month, Year, Total
- Battery Charge: Day, Month, Year, Total
- Battery Discharge: Day, Month, Year, Total

**Rate Sensors** (%) for Day, Month, Year, and Total periods:
- Cover Rate
- Battery Charge Rate
- Energy Self-Sufficiency Rate
- Grid Export Rate
- Battery Discharge Rate
- Consumption from PV Rate
- Grid Import Rate

**Device Information**:
- Installed Capacity (W)

### Device Sensors (Per Inverter/Equipment)

**Connectivity**:
- Online State
- WiFi Signal Strength (RSSI in dBm)
- WiFi Network Name
- IP Address
- Upgrade (Available/None) - Indicates if a firmware update is available for this device

**PV Production**:
- Individual PV string power (PV1, PV2, etc.) in Watts

**CT Sensors Smart IA**:
- CT2 and CT3 are listed when a smart meter is configured and configured for single-phase power, in Watts

**Device-Specific Sensors** (depending on device type):
- Temperature (°C) - for micro-inverters and batteries
- Average State of Charge (%) - for devices with integrated battery
- Cluster Mode - for devices in multi-inverter configuration (Master/Slave/Standalone)

### Battery Device Sensors (Per Battery with Modules)

For batteries with individual modules/Link, additional sub-devices are created:

**Parent Battery Device**:
- State of Charge (%)
- Energy (kWh)
- Charge from External Power (W)
- Charge from PV (W)
- Charging Time Remaining (min)
- Discharge from Battery (W)
- Discharge from PV (W)
- Discharging Time Remaining (min)
- Power Consumption (W)
- Battery Device Power (W)
- Solar Surplus Power (W)
- Direct Solar Power (W)
- Backup Outlet Power (W)

**Battery Link Sub-Devices** (per individual battery module):
- State of Charge (%)
- Energy (kWh)

### Battery Device Controls

For all battery devices, the following controls are available:

**LED Light Control Buttons**:
- Turn On Lights: Activate the battery LED indicator lights
- Turn Off Lights: Deactivate the battery LED indicator lights

**Minimum Discharge Level Number**:
- Configure the minimum state of charge (5-100%) below which the battery will not discharge
- Protects battery health by preventing excessive discharge
- Setting applies to the battery device and all its modules

**Advanced Battery Controls**:
- The controls below are available only for batteries in Master (cluster mode) or Standalone
- Control Mode: `Intelligent`, `Manual`, `Calendar`
- The `Intelligent` option is shown only when the station has a compatible smart meter
- Manual Mode: `Standby`, `Charge`, `Discharge`
- Max Charge Power: sets the maximum allowed battery charging power
- Max Discharge Power: sets the maximum allowed battery discharging power
- Power Charge (Manual): available only when manual mode is `Charge`
- Power Discharge (Manual): available only when manual mode is `Discharge`
- When manual mode is `Standby`, both manual power fields are unavailable
- Manual power fields use a dynamic range from `0` to the corresponding max power value, with `50 W` increments

**Backup Outlet (Off-Grid) Controls**:
- Backup Outlet Toggle: Enable/disable the backup outlet for off-grid operation
- Backup Outlet Mode Select: Choose the off-grid operation mode
  - `Invester`: Micro-inverter mode
  - `Always`: Always provide backup power
  - `When power cut`: Provide backup power only during grid outages
- Mode select is only available when the Backup Outlet toggle is enabled

**Calibration Controls**:
- Calibration Toggle: Enable/disable battery calibration (full charge mode)
- Calibration Interval Number: Set the calibration interval between 5 and 60 days (1-day increments)
- Calibration Time Select: Set the time when calibration should occur (from 00:00 to 23:55, 5-minute increments)
- Both the interval number and time select are only available when the Calibration toggle is enabled
- Calibration helps maintain battery health by periodically performing full charge/discharge cycles

### Meter Device Controls

For smart meter devices, the following controls are available:

**Injection Blocking Switch and Injection Limit Number**:
- Enable/disable grid injection control
- When enabled, restricts power export to the grid according to the configured injection limit
- Configure the maximum power injection to the grid (displayed as positive watts, 0-36000W)


### Technical Features

- Cloud polling: Data retrieved via Izypower Cloud API
- Configuration via Home Assistant config flow and options flow
- Customizable refresh period
- Automatic discovery of stations and devices
- Code owner: @StefanPlizga

### Documentation & Support

- [Official documentation](https://github.com/StefanPlizga/izypower_cloud/blob/main/README.md)
- [Issue tracker](https://github.com/StefanPlizga/izypower_cloud/issues)

- **Automatic token refresh**: Authentication tokens are managed automatically
- **Robust retry logic**: Network errors handled with exponential backoff and jitter
- **Real-time updates**: All data refreshed at configured interval
- **Credential validation**: Setup validation with automatic reauth flow if needed
- **Persistent notifications**: Alerts if credentials expire or become invalid
- **Multi-language support**: English and French translations included

## Device Organization

- **Power Station Device**: Main device containing station-level sensors (power, energy, rates, capacity, battery state of charge, last update)
- **Inverter/Equipment Sub-devices**: Each inverter/equipment under the station with device-specific sensors (online state, WiFi, PV strings, average state of charge, cluster mode)
- **Battery Sub-devices**: For batteries with modules, a parent battery device with energy and state of charge sensors
- **Battery Link Sub-devices**: For each individual battery module, a sub-device with its own state of charge and energy
- **Battery Device Controls**: LED light buttons and minimum discharge level for all batteries, plus advanced controls (control mode, manual mode, max power, and manual power fields) for Master/Standalone batteries, backup outlet controls (toggle and mode select) for all batteries, and calibration controls (toggle, interval, and time) for all batteries
- **Meter Devices**: For smart meters, injection control switch and injection limit number entities for managing grid export
- **Logical grouping**: All sensors properly categorized with appropriate device classes and state classes for Home Assistant Energy dashboard compatibility

## Data Refresh

- Default refresh interval: **3 minutes**
- All sensors update simultaneously during each refresh cycle
- Coordinator fetches:
  - Station list and information
  - Real-time power data
  - Station battery state of charge and timestamp
  - Energy statistics (daily, monthly, yearly, total)
  - Rate percentages
  - Device status and WiFi information
  - Individual PV string production
  - CT2 and CT3 (only when a smart meter is configured and monophased)
  - Battery data

## Notes

- Rate sensors automatically parse percentage values from the API
- WiFi information only available for devices with serial numbers
- Average state of charge sensor only appears for devices with integrated battery
- Cluster mode sensor only appears for devices in multi-inverter configuration
- Battery Link sub-devices are automatically created for batteries with individual modules
- CT2 and CT3 are listed only when a smart meter is configured and monophased.
- If credentials expire, a persistent notification will prompt reauthentication
