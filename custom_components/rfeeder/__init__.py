from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from . import proto
from .api import UcspClient
from .const import (
    CONF_AREA,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_PRODUCT_KEY,
    CONF_PW_SALT,
    CONF_TIMESTAMP_URL,
    DOMAIN,
    PLATFORMS,
    SERVICE_ADD_SCHEDULE,
    SERVICE_FEED_NOW,
    SERVICE_REMOVE_SCHEDULE,
    SERVICE_SET_SCHEDULE_ENABLED,
)
from .coordinator import RFeederCoordinator
from .mqtt_client import MqttError

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_ID = "device_id"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config = {
        CONF_AREA: entry.data.get(CONF_AREA),
        CONF_APP_ID: entry.data.get(CONF_APP_ID),
        CONF_APP_SECRET: entry.data.get(CONF_APP_SECRET),
        CONF_BASE_URL: entry.data.get(CONF_BASE_URL),
        CONF_CLIENT_ID: entry.data.get(CONF_CLIENT_ID),
        CONF_PRODUCT_KEY: entry.data.get(CONF_PRODUCT_KEY),
        CONF_PW_SALT: entry.data.get(CONF_PW_SALT),
        CONF_TIMESTAMP_URL: entry.data.get(CONF_TIMESTAMP_URL),
    }
    missing = [key for key, value in config.items() if key != CONF_AREA and not value]
    if missing:
        _LOGGER.error("RFeeder setup missing API field(s): %s", ", ".join(missing))
        return False

    # Dedicated session: the client intentionally drops pooled connections to
    # work around the vendor's inconsistent DNS round-robin backends; that must
    # not affect the shared HA session used by other integrations.
    session = aiohttp_client.async_create_clientsession(hass)
    client = UcspClient(
        session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        config=config,
    )
    coordinator = RFeederCoordinator(hass, client, entry.data[CONF_PRODUCT_KEY], entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    coordinator.start_mqtt()
    entry.async_on_unload(coordinator.stop_mqtt)

    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            coordinator.stop_mqtt()
    return unload_ok


def _coordinators(hass: HomeAssistant) -> list[RFeederCoordinator]:
    return list(hass.data.get(DOMAIN, {}).values())


def _find_device(hass: HomeAssistant, device_id: str | None) -> tuple[RFeederCoordinator, str]:
    """Resolve the (coordinator, device_id) for a service call."""
    for coordinator in _coordinators(hass):
        devices = (coordinator.data or {}).get("devices", {})
        if device_id is None and devices:
            return coordinator, next(iter(devices))
        if device_id in devices:
            return coordinator, device_id
    raise HomeAssistantError(f"Unknown RFeeder device: {device_id}")


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_FEED_NOW):
        return

    async def handle_feed_now(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        prefs = {}
        entry = coordinator.data["devices"][device_id].get("shadow", {}).get("10:11:7")
        if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
            prefs = entry["value"]
        tray = coordinator.data["devices"][device_id].get("shadow", {}).get("10:11:6", {})
        tray_index = tray.get("value", 1) if isinstance(tray, dict) else 1
        try:
            tray_default = int(tray_index)
        except (TypeError, ValueError):
            tray_default = 1
        try:
            await coordinator.async_feed_now(
                device_id,
                tray_compartment_index=call.data.get(
                    "tray_compartment_index",
                    coordinator.get_compartment(device_id, tray_default),
                ),
                feed_duration_seconds=call.data.get(
                    "feed_duration_seconds", prefs.get("feedDurationSeconds", 300)
                ),
                heat_before_feeding=call.data.get("heat_before_feeding", False),
                expected_heating_temperature=call.data.get(
                    "expected_heating_temperature",
                    prefs.get("expectedHeatingTemperature", 24),
                ),
            )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_add_schedule(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        schedule = {
            "id": 0,
            "hour": call.data["hour"],
            "minute": call.data["minute"],
            "enabled": call.data.get("enabled", True),
            "weekdays": call.data.get("weekdays", 0),
            "feedOptions": {
                "trayCompartmentIndex": call.data.get("tray_compartment_index", 1),
                "feedDurationSeconds": call.data.get("feed_duration_seconds", 300),
                "heatBeforeFeeding": call.data.get("heat_before_feeding", False),
                "expectedHeatingTemperature": call.data.get("expected_heating_temperature", 24),
            },
        }
        try:
            await coordinator.async_set_schedules(
                device_id, proto.SERVICE_ADD_SCHEDULES, [schedule]
            )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_remove_schedule(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        try:
            await coordinator.async_remove_schedules(device_id, [int(call.data["schedule_id"])])
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_set_schedule_enabled(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        schedule_id = int(call.data["schedule_id"])
        enabled = bool(call.data["enabled"])
        shadow = coordinator.data["devices"][device_id].get("shadow", {})
        entry = shadow.get("10:11:13")
        schedules = []
        if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
            schedules = entry["value"].get("schedules") or []
        updated = []
        found = False
        for item in schedules:
            if not isinstance(item, dict):
                continue
            if item.get("id") == schedule_id:
                start = item.get("start") or {}
                options = item.get("feedOptions") or {}
                updated.append(
                    {
                        "id": schedule_id,
                        "hour": start.get("hour", 0),
                        "minute": start.get("minute", 0),
                        "enabled": enabled,
                        "weekdays": item.get("weekdays", 0),
                        "feedOptions": {
                            "trayCompartmentIndex": options.get("trayCompartmentIndex", 1),
                            "feedDurationSeconds": options.get("feedDurationSeconds", 300),
                            "heatBeforeFeeding": options.get("heatBeforeFeeding", False),
                            "expectedHeatingTemperature": options.get(
                                "expectedHeatingTemperature", 24
                            ),
                        },
                    }
                )
                found = True
            else:
                start = item.get("start") or {}
                options = item.get("feedOptions") or {}
                updated.append(
                    {
                        "id": item.get("id", 0),
                        "hour": start.get("hour", 0),
                        "minute": start.get("minute", 0),
                        "enabled": item.get("enabled", True),
                        "weekdays": item.get("weekdays", 0),
                        "feedOptions": {
                            "trayCompartmentIndex": options.get("trayCompartmentIndex", 1),
                            "feedDurationSeconds": options.get("feedDurationSeconds", 300),
                            "heatBeforeFeeding": options.get("heatBeforeFeeding", False),
                            "expectedHeatingTemperature": options.get(
                                "expectedHeatingTemperature", 24
                            ),
                        },
                    }
                )
        if not found:
            raise HomeAssistantError(f"Schedule {schedule_id} not found")
        try:
            await coordinator.async_set_schedules(
                device_id, proto.SERVICE_UPDATE_SCHEDULES, updated
            )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_FEED_NOW,
        handle_feed_now,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Optional("tray_compartment_index"): vol.Coerce(int),
                vol.Optional("feed_duration_seconds"): vol.Coerce(int),
                vol.Optional("heat_before_feeding"): bool,
                vol.Optional("expected_heating_temperature"): vol.Coerce(float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_SCHEDULE,
        handle_add_schedule,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required("minute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
                vol.Optional("weekdays", default=0): vol.Coerce(int),
                vol.Optional("enabled", default=True): bool,
                vol.Optional("tray_compartment_index", default=1): vol.Coerce(int),
                vol.Optional("feed_duration_seconds", default=300): vol.Coerce(int),
                vol.Optional("heat_before_feeding", default=False): bool,
                vol.Optional("expected_heating_temperature", default=24): vol.Coerce(float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_SCHEDULE,
        handle_remove_schedule,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Required("schedule_id"): vol.Coerce(int),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_ENABLED,
        handle_set_schedule_enabled,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Required("schedule_id"): vol.Coerce(int),
                vol.Required("enabled"): bool,
            }
        ),
    )
