from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_APP_SECRET, CONF_PW_SALT, DOMAIN

TO_REDACT = {CONF_PASSWORD, CONF_APP_SECRET, CONF_PW_SALT, "token", "accessToken", "refreshToken"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "data": coordinator.data,
    }
