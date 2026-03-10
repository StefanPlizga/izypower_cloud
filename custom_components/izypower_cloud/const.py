from datetime import timedelta

DOMAIN = "izypower_cloud"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=3)

LOGIN_URL = "http://application.izypowercloud.fr/photo_voltaic/api/login"
STATIONS_URL = "http://application.izypowercloud.fr/photo_voltaic/api/powerStations/page"
DEVICE_PAGE_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/device/page?powerId={component_id}&deviceType={device_type}&page={page}&limit={limit}"
COMPONENT_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/component/{component_id}?searchTime={date}"
STATION_INFO_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/v3/powerStations/info/{component_id}"
REPORT_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/report/v2/powerStations/data/{component_id}?timeType={time_type}&dataFlag=energy&searchTime={date}"
DEVICE_WIFI_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/v3/device/wifi/{serial_number}"
BATTERY_LINKS_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/izy/v2/battery/{serial_number}"
DEVICE_TEMP_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/report/device/data/{serial_number}?searchTime={date}&timeType=day&dataFlag=temp"
DEVICE_UPGRADE_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/v3/device/upgrade/{station_id}"
METER_BASE_INFO_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/v2/device/baseInfo/{device_id}"
METER_CONTROL_URL_TEMPLATE = "http://application.izypowercloud.fr/photo_voltaic/api/v2/device/meter/control/{serial_number}"

TOKEN_HEADER = "x-tts-access-token"
APP_PLATFORM_HEADER = "izy"

ENTITY_ID_PREFIX = "izypower_cloud"
DISPLAY_NAME_PREFIX = "Izypower Cloud"
