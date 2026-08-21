from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import aiohttp_client, selector

from .api import UcspApiError, UcspAuthError, UcspClient
from .const import (
    AREA_CODES,
    CONF_AREA,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_PRODUCT_KEY,
    CONF_PW_SALT,
    CONF_TIMESTAMP_URL,
    DEFAULT_APP_ID,
    DEFAULT_APP_SECRET,
    DEFAULT_AREA,
    DEFAULT_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_PRODUCT_KEY,
    DEFAULT_PW_SALT,
    DEFAULT_TIMESTAMP_URL,
    DOMAIN,
)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_AREA, default=defaults.get(CONF_AREA, DEFAULT_AREA)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=code, label=name)
                        for code, name in AREA_CODES.items()
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _clean(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required value is empty")
    return value.strip()


class RFeederConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                username = _clean(user_input.get(CONF_USERNAME))
                password = _clean(user_input.get(CONF_PASSWORD))
                area = _clean(user_input.get(CONF_AREA)).upper()
            except ValueError:
                errors["base"] = "missing_required"
            else:
                config = {
                    CONF_AREA: area,
                    CONF_APP_ID: DEFAULT_APP_ID,
                    CONF_APP_SECRET: DEFAULT_APP_SECRET,
                    CONF_BASE_URL: DEFAULT_BASE_URL,
                    CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
                    CONF_PRODUCT_KEY: DEFAULT_PRODUCT_KEY,
                    CONF_PW_SALT: DEFAULT_PW_SALT,
                    CONF_TIMESTAMP_URL: DEFAULT_TIMESTAMP_URL,
                }
                session = aiohttp_client.async_create_clientsession(self.hass)
                client = UcspClient(
                    session, account=username, password=password, config=config
                )
                try:
                    await client.async_login()
                    devices = await client.get_devices(DEFAULT_PRODUCT_KEY)
                except UcspAuthError:
                    errors["base"] = "invalid_auth"
                except UcspApiError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(f"{DOMAIN}_{username.lower()}")
                    self._abort_if_unique_id_configured()
                    title = "RFeeder"
                    if devices:
                        title = devices[0].get("deviceName") or devices[0].get("deviceId") or title
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            **config,
                        },
                    )

        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)
