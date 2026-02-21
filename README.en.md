# Izypower Cloud Home Assistant Integration

This custom integration automatically discovers all Izypower Cloud power stations and provides comprehensive monitoring of your solar installation.

> **Important Note**: This is a community integration and is not developped by Materfrance.

## Acknowledgments

Thanks to Khirale, MarcoCMG, Wellgo and Zyos67 for testing and feedback.

## Installation

### Via HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance
2. Click the button below to add this repository to HACS:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=StefanPlizga&repository=izypower_cloud&category=integration)

   Or manually:
   - In HACS, click on "Integrations"
   - Click the menu (three dots) in the top right and select "Custom repositories"
   - Add `https://github.com/StefanPlizga/izypower_cloud` as a repository with category "Integration"

3. Search for "Izypower Cloud" in HACS and click "Download"
4. Restart Home Assistant
5. Click the button below to add the integration:

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

**Energy Sensors** (kWh):
- Production: Day, Month, Year, Total
- Grid Import: Day, Month, Year, Total
- Grid Export: Day, Month, Year, Total
- Consumption: Day, Month, Year, Total
- Consumption from PV: Day, Month, Year, Total (calculated)
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

**PV Production**:
- Individual PV string power (PV1, PV2, etc.) in Watts

**Device-Specific Sensors** (depending on device type):
- Average State of Charge (%) - for devices with integrated battery
- Cluster Mode - for devices in multi-inverter configuration (Master/Slave/Standalone)

### Battery Device Sensors (Per Battery with Modules)

For batteries with individual modules/Link, additional sub-devices are created:

**Parent Battery Device**:
- State of Charge (%)
- Energy (kWh)

**Battery Link Sub-Devices** (per individual battery module):
- State of Charge (%)
- Energy (kWh)

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
  - Battery data

## Notes

- All energy sensors are compatible with Home Assistant's Energy dashboard
- Rate sensors automatically parse percentage values from the API
- Calculated sensors (like Consumption from PV) ensure non-negative values
- WiFi information only available for devices with serial numbers
- Average state of charge sensor only appears for devices with integrated battery
- Cluster mode sensor only appears for devices in multi-inverter configuration
- Battery Link sub-devices are automatically created for batteries with individual modules
- If credentials expire, a persistent notification will prompt reauthentication
