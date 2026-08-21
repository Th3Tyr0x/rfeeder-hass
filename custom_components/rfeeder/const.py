from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "rfeeder"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)

try:
    from .secrets import (
        APP_ID as DEFAULT_APP_ID,
        APP_SECRET as DEFAULT_APP_SECRET,
        BASE_URL as DEFAULT_BASE_URL,
        CLIENT_ID as DEFAULT_CLIENT_ID,
        PRODUCT_KEY as DEFAULT_PRODUCT_KEY,
        PW_SALT as DEFAULT_PW_SALT,
        TIMESTAMP_URL as DEFAULT_TIMESTAMP_URL,
    )
except ImportError:
    DEFAULT_APP_ID = ""
    DEFAULT_APP_SECRET = ""
    DEFAULT_BASE_URL = ""
    DEFAULT_CLIENT_ID = ""
    DEFAULT_PRODUCT_KEY = ""
    DEFAULT_PW_SALT = ""
    DEFAULT_TIMESTAMP_URL = ""

CONF_AREA = "area"
CONF_APP_ID = "app_id"
CONF_APP_SECRET = "app_secret"
CONF_CLIENT_ID = "client_id"
CONF_BASE_URL = "base_url"
CONF_PRODUCT_KEY = "product_key"
CONF_PW_SALT = "pw_salt"
CONF_TIMESTAMP_URL = "timestamp_url"

DEFAULT_AREA = "DE"

# Country codes with their UCSP regional hosts, as returned by
# GET /usercenter/v1/area-domains on the global host (DE verified live).
AREA_CODES: dict[str, str] = {
    "DE": "Germany",
    "AT": "Austria",
    "CH": "Switzerland",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "PL": "Poland",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "PT": "Portugal",
    "CZ": "Czech Republic",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "HU": "Hungary",
    "RO": "Romania",
    "BG": "Bulgaria",
    "HR": "Croatia",
    "GR": "Greece",
    "EE": "Estonia",
    "LV": "Latvia",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MT": "Malta",
    "CY": "Cyprus",
    "IS": "Iceland",
    "LI": "Liechtenstein",
    "MC": "Monaco",
    "SM": "San Marino",
    "VA": "Vatican City",
    "AD": "Andorra",
    "AL": "Albania",
    "BA": "Bosnia and Herzegovina",
    "ME": "Montenegro",
    "MK": "North Macedonia",
    "RS": "Serbia",
    "XK": "Kosovo",
    "MD": "Moldova",
    "UA": "Ukraine",
    "BY": "Belarus",
    "RU": "Russia",
    "TR": "Turkey",
    "IL": "Israel",
    "AE": "United Arab Emirates",
    "ZA": "South Africa",
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
    "AU": "Australia",
    "NZ": "New Zealand",
    "JP": "Japan",
    "KR": "South Korea",
    "SG": "Singapore",
    "MY": "Malaysia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "TW": "Taiwan",
    "FO": "Faroe Islands",
    "GI": "Gibraltar",
}

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]

# HA service names
SERVICE_FEED_NOW = "feed_now"
SERVICE_ADD_SCHEDULE = "add_schedule"
SERVICE_REMOVE_SCHEDULE = "remove_schedule"
SERVICE_SET_SCHEDULE_ENABLED = "set_schedule_enabled"
