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
