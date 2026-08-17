"""Data update coordinator for the RFeeder integration.

Polls the UCSP REST API (device shadow, records) and keeps a persistent MQTT
connection in a background thread for live state pushes and device commands.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import queue
import random
import threading
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UcspApiError, UcspClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .mqtt_client import MqttError, UcspMqttClient
from . import proto

_LOGGER = logging.getLogger(__name__)

MQTT_RECONNECT_MIN_SECONDS = 15
MQTT_RECONNECT_MAX_SECONDS = 300
COMMAND_TIMEOUT_SECONDS = 15

# attribute id -> decoder kind for the feed cluster (11).
# Note the vendor's quirk: the cooling target is transmitted/stored in
# half-degrees Celsius (levels: 14/17/20 = 7/8.5/10 °C) while the actual
# temperature sensors are plain °C.
_HALF_DEG_ATTRIBUTES = {proto.ATTR_EXPECTED_COOLING_TEMPERATURE}
_PLAIN_INT_ATTRIBUTES = {
    proto.ATTR_ACTUAL_TEMPERATURE,
    proto.ATTR_THERMOELECTRIC_TEMPERATURE,
    proto.ATTR_TRAY_TEMPERATURE,
    proto.ATTR_TRAY_COMPARTMENT_COUNT,
    proto.ATTR_TRAY_FEEDABLE_COMPARTMENT,
    proto.ATTR_PREHEAT_DURATION_MINUTES,
}
_ENUM_ATTRIBUTES = {
    proto.ATTR_GLOBAL_STATE: proto.GLOBAL_STATES,
    proto.ATTR_FEED_STATE: proto.FEED_STATES,
    proto.ATTR_DOOR_STATE: proto.DOOR_STATES,
    proto.ATTR_TRAY_STATE: proto.TRAY_STATES,
    proto.ATTR_TEMPERATURE_TREND: proto.TEMPERATURE_TRENDS,
}
_BOOL_ATTRIBUTES = {
    proto.ATTR_PET_DETECTION_ON,
    proto.ATTR_PET_DETECTED,
    proto.ATTR_TEMPERATURE_CONTROL_ON,
    proto.ATTR_AUTO_FEEDING_ENABLED,
    proto.ATTR_HEAT_BEFORE_FEED_NOW,
}
_DEVICE_ENUM_ATTRIBUTES = {3: proto.TRANSFER_STATES, 6: proto.UPDATE_STATES, 7: proto.POWER_SUPPLIES}


def shadow_key(cluster: int, attribute: int, endpoint: int = proto.ENDPOINT_MAIN) -> str:
    return f"{endpoint}:{cluster}:{attribute}"


def _normalize_shadow(shadow: Any) -> None:
    """Normalize vendor quirks in the REST shadow in-place (values -> °C)."""
    if not isinstance(shadow, dict):
        return
    entry = shadow.get(shadow_key(proto.CLUSTER_FEED, proto.ATTR_EXPECTED_COOLING_TEMPERATURE))
    if isinstance(entry, dict) and isinstance(entry.get("value"), (int, float)):
        entry["value"] = entry["value"] / 2


def decode_attribute_value(cluster: int, attribute: int, data: bytes) -> Any:
    """Decode a raw MQTT attribute report into the REST shadow value shape."""
    if cluster == proto.CLUSTER_FEED:
        if attribute in _ENUM_ATTRIBUTES:
            return _ENUM_ATTRIBUTES[attribute].get(proto.decode_primitive_data(data))
        if attribute in _BOOL_ATTRIBUTES:
            return bool(proto.decode_primitive_data(data))
        if attribute in _HALF_DEG_ATTRIBUTES:
            return proto.temp_from_wire(proto.decode_primitive_data(data))
        if attribute in _PLAIN_INT_ATTRIBUTES:
            return proto.decode_primitive_data(data)
        if attribute == proto.ATTR_PREFERENCES:
            return proto.decode_feed_preferences(data)
        if attribute in (proto.ATTR_FEEDING_SCHEDULES, proto.ATTR_EXECUTING_FEEDING_SCHEDULE):
            return {"schedules": proto.decode_feeding_schedule_list(data)}
    if cluster == proto.CLUSTER_DEVICE:
        if attribute in _DEVICE_ENUM_ATTRIBUTES:
            return _DEVICE_ENUM_ATTRIBUTES[attribute].get(proto.decode_primitive_data(data))
    return proto.decode_primitive_data(data)


class RFeederCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: UcspClient,
        product_key: str,
        config_entry: Any = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
            always_update=True,
            config_entry=config_entry,
        )
        self.client = client
        self.product_key = product_key
        # UI state: which tray compartment to feed from (chosen per device)
        self._selected_compartment: dict[str, int] = {}
        self._mqtt_thread: threading.Thread | None = None
        self._mqtt_stop = threading.Event()
        self._command_queue: queue.Queue[tuple[dict[str, Any], asyncio.Future]] = queue.Queue()
        self._message_id = random.randint(1, 100)
        self._mqtt_lock = threading.Lock()
        self._mqtt_client: UcspMqttClient | None = None

    # ------------------------------------------------------------------ REST

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            devices = await self.client.get_devices(self.product_key)
            result: dict[str, Any] = {"user_id": self.client.user_id, "devices": {}}
            now_ms = int(time.time() * 1000)
            day_start_ms = now_ms - 86_400_000
            for device in devices:
                device_id = device.get("deviceId")
                if not device_id:
                    continue
                entry: dict[str, Any] = {"info": device}
                for key, coro in (  # noqa: B007 - key used for assignment below

                    ("shadow", self.client.get_shadow(self.product_key, device_id)),
                    ("online", self.client.get_onoffline(self.product_key, device_id)),
                    (
                        "today",
                        self.client.get_feeding_records_today(
                            self.product_key, device_id, day_start_ms, now_ms
                        ),
                    ),
                    (
                        "statistics",
                        self.client.get_plate_statistics(
                            self.product_key, device_id, day_start_ms, now_ms
                        ),
                    ),
                    (
                        "records",
                        self.client.get_feeding_records(
                            self.product_key, device_id, day_start_ms, now_ms, size=10
                        ),
                    ),
                ):
                    try:
                        entry[key] = await coro
                    except UcspApiError as err:
                        _LOGGER.debug("Optional endpoint %s failed: %s", key, err)
                        entry[key] = None
                result["devices"][device_id] = entry
                _normalize_shadow(entry.get("shadow"))
            return self._merge_live(result)
        except UcspApiError as err:
            raise UpdateFailed(str(err)) from err

    # ------------------------------------------------------------------ MQTT

    def start_mqtt(self) -> None:
        if self._mqtt_thread is None or not self._mqtt_thread.is_alive():
            self._mqtt_stop.clear()
            self._mqtt_thread = threading.Thread(
                target=self._mqtt_loop, name="rfeeder-mqtt", daemon=True
            )
            self._mqtt_thread.start()

    def stop_mqtt(self) -> None:
        self._mqtt_stop.set()
        with self._mqtt_lock:
            if self._mqtt_client is not None:
                self._mqtt_client.close()
        if self._mqtt_thread is not None:
            self._mqtt_thread.join(timeout=10)
            self._mqtt_thread = None

    def _mqtt_loop(self) -> None:
        backoff = MQTT_RECONNECT_MIN_SECONDS
        while not self._mqtt_stop.is_set():
            try:
                self._mqtt_connect_and_run()
                backoff = MQTT_RECONNECT_MIN_SECONDS
            except Exception as err:  # noqa: BLE001 - keep the thread alive
                if not self._mqtt_stop.is_set():
                    _LOGGER.info("MQTT connection lost: %s; retry in %ss", err, backoff)
            if self._mqtt_stop.wait(backoff):
                break
            backoff = min(backoff * 2, MQTT_RECONNECT_MAX_SECONDS)

    def _get_credentials_sync(self) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(
            self.client.get_mqtt_credentials(), self.hass.loop
        )
        return future.result(timeout=30)

    def _mqtt_connect_and_run(self) -> None:
        creds = self._get_credentials_sync()
        url = creds["mqttUrl"].replace("mqtts://", "").replace("mqtt://", "")
        host, _, port = url.partition(":")
        client = UcspMqttClient(host, int(port or 20000))
        with self._mqtt_lock:
            self._mqtt_client = client
        try:
            client.connect(
                client_id=f"ha-{self.client.user_id}-{random.randint(1000, 9999)}",
                username=creds["username"],
                password=creds["password"],
            )
            devices = (self.data or {}).get("devices", {})
            uid = self.client.user_id
            for device_id in devices:
                base = f"/{self.product_key}/{device_id}"
                client.subscribe(f"{base}/thing/property/post")
                client.subscribe(f"{base}/thing/event/post")
                client.subscribe(f"{base}/onoffline")
                client.subscribe(f"{base}/{uid}/upload/reply")
            _LOGGER.info("MQTT connected to %s:%s", host, port)
            while not self._mqtt_stop.is_set():
                self._run_pending_commands(client)
                message = client.read_message(timeout=1.0)
                if message is None:
                    continue
                topic, payload = message
                self._handle_mqtt_message(topic, payload)
        finally:
            with self._mqtt_lock:
                self._mqtt_client = None
            client.close()

    def _run_pending_commands(self, client: UcspMqttClient) -> None:
        while True:
            try:
                command, future = self._command_queue.get_nowait()
            except queue.Empty:
                return
            try:
                result = self._execute_command(client, command)
                self._set_future_result(future, result)
            except Exception as err:  # noqa: BLE001 - propagate to caller
                self._set_future_exception(future, err)

    def _set_future_result(self, future: asyncio.Future, result: Any) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: not future.done() and future.set_result(result)
        )

    def _set_future_exception(self, future: asyncio.Future, err: Exception) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: not future.done() and future.set_exception(err)
        )

    def _execute_command(self, client: UcspMqttClient, command: dict[str, Any]) -> dict[str, Any]:
        device_id = command["device_id"]
        uid = str(self.client.user_id)
        # The device firmware only accepts 16-bit exchange ids (the app's
        # DefaultExchangeIdGenerator uses Random.nextInt(0x10000)); larger
        # values are silently dropped.
        exchange_id = random.randint(1, 0xFFFF)
        self._message_id += 1
        if command["kind"] == "invoke":
            payload = proto.encode_invoke_request(
                proto.ENDPOINT_MAIN, command["cluster"], command["service"], command["arg"]
            )
            topic_suffix = "thing/service"
            opcode = proto.OPCODE_INVOKE_REQUEST
        else:
            payload = proto.encode_write_request(
                proto.ENDPOINT_MAIN, command["cluster"], command["attributes"]
            )
            topic_suffix = "thing/property/set"
            opcode = proto.OPCODE_WRITE_REQUEST
        interaction = proto.encode_interaction(
            message_id=self._message_id,
            exchange_id=exchange_id,
            source_node_id=uid,
            destination_node_id=device_id,
            timestamp_ms=int(time.time() * 1000),
            opcode=opcode,
            payload=payload,
        )
        topic = f"/{self.product_key}/{device_id}/{uid}/{topic_suffix}"
        client.publish(topic, interaction, qos=1)
        # wait for the matching reply on upload/reply
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            message = client.read_message(timeout=max(0.2, deadline - time.monotonic()))
            if message is None:
                break
            msg_topic, msg_payload = message
            if msg_topic.endswith("/upload/reply"):
                reply = proto.decode_interaction(msg_payload)
                if reply.exchange_id == exchange_id:
                    _LOGGER.debug("Command reply: %s", reply)
                    return {"status": "ok", "opcode": reply.opcode}
            else:
                self._handle_mqtt_message(msg_topic, msg_payload)
        raise MqttError("command timed out waiting for device reply")

    def _handle_mqtt_message(self, topic: str, payload: bytes) -> None:
        try:
            if topic.endswith("/onoffline"):
                self._handle_onoffline(topic, payload)
                return
            interaction = proto.decode_interaction(payload)
            if interaction.opcode != proto.OPCODE_REPORT_DATA:
                return
            attributes, events = proto.decode_report_data(interaction.payload)
            if attributes:
                self.hass.loop.call_soon_threadsafe(self._apply_attribute_reports, attributes)
            if events:
                self.hass.loop.call_soon_threadsafe(self._apply_event_reports, events)
        except (ValueError, KeyError) as err:
            _LOGGER.debug("Could not decode MQTT message on %s: %s", topic, err)

    # ------------------------------------------------------- live data merge

    def _merge_live(self, data: dict[str, Any]) -> dict[str, Any]:
        live = getattr(self, "_live_shadow", {})
        for device_id, entry in data.get("devices", {}).items():
            device_live = live.get(device_id)
            if not device_live:
                continue
            shadow = entry.get("shadow")
            if not isinstance(shadow, dict):
                continue
            for key, value in device_live.items():
                shadow[key] = value
        return data

    def _apply_attribute_reports(self, reports: list[Any]) -> None:
        if not self.data:
            return
        live = getattr(self, "_live_shadow", None)
        if live is None:
            live = self._live_shadow = {}
        now_ms = int(time.time() * 1000)
        for report in reports:
            for device_id in self.data.get("devices", {}):
                value = decode_attribute_value(report.cluster, report.attribute, report.data)
                device_live = live.setdefault(device_id, {})
                device_live[shadow_key(report.cluster, report.attribute)] = {
                    "time": now_ms,
                    "value": value,
                }
        self._merge_live(self.data)
        self.async_update_listeners()

    def _apply_event_reports(self, events: list[Any]) -> None:
        # events are informational; keep the last one per device for attributes
        if not self.data:
            return
        for event in events:
            for device_id, entry in self.data.get("devices", {}).items():
                entry["last_event"] = {
                    "event": event.event,
                    "cluster": event.cluster,
                    "time": event.timestamp_ms,
                    "data": event.data.hex(),
                }

    def _handle_onoffline(self, topic: str, payload: bytes) -> None:
        if not self.data:
            return
        parts = topic.strip("/").split("/")
        if len(parts) < 2:
            return
        device_id = parts[1]
        entry = self.data.get("devices", {}).get(device_id)
        if entry is None:
            return
        try:
            text = payload.decode("utf-8", "replace").strip()
            online = text in ("1", "online", "true")
        except ValueError:
            return

        def _apply() -> None:
            entry["online"] = [{"deviceId": device_id, "onoffline": 1 if online else 0}]
            self.async_update_listeners()

        self.hass.loop.call_soon_threadsafe(_apply)

    # -------------------------------------------------------------- commands

    def get_compartment(self, device_id: str, default: int = 1) -> int:
        """Tray compartment selected in the UI for the next feeding."""
        return self._selected_compartment.get(device_id, default)

    def set_compartment(self, device_id: str, index: int) -> None:
        self._selected_compartment[device_id] = index

    async def _async_send_command(self, command: dict[str, Any]) -> Any:
        if self._mqtt_thread is None or not self._mqtt_thread.is_alive():
            raise MqttError("MQTT is not connected")
        future: asyncio.Future = self.hass.loop.create_future()
        self._command_queue.put((command, future))
        return await asyncio.wait_for(future, COMMAND_TIMEOUT_SECONDS + 5)

    async def async_feed_now(
        self,
        device_id: str,
        *,
        tray_compartment_index: int,
        feed_duration_seconds: int,
        heat_before_feeding: bool,
        expected_heating_temperature: float,
    ) -> None:
        arg = proto.encode_feed_options(
            tray_compartment_index=tray_compartment_index,
            feed_duration_seconds=feed_duration_seconds,
            heat_before_feeding=heat_before_feeding,
            expected_heating_temperature=expected_heating_temperature,
            operator=self.client.user_id or 0,
        )
        await self._async_send_command(
            {
                "kind": "invoke",
                "device_id": device_id,
                "cluster": proto.CLUSTER_FEED,
                "service": proto.SERVICE_FEED,
                "arg": arg,
            }
        )
        await self.async_request_refresh()

    async def async_simple_service(self, device_id: str, service: int) -> None:
        await self._async_send_command(
            {
                "kind": "invoke",
                "device_id": device_id,
                "cluster": proto.CLUSTER_FEED,
                "service": service,
                "arg": b"",
            }
        )
        await self.async_request_refresh()

    async def async_write_attributes(
        self, device_id: str, cluster: int, attributes: dict[int, bytes]
    ) -> None:
        await self._async_send_command(
            {
                "kind": "write",
                "device_id": device_id,
                "cluster": cluster,
                "attributes": attributes,
            }
        )
        await self.async_request_refresh()

    async def async_set_schedules(
        self, device_id: str, service: int, schedules: list[dict[str, Any]]
    ) -> None:
        encoded = []
        for schedule in schedules:
            options = schedule["feedOptions"]
            encoded.append(
                proto.encode_feeding_schedule(
                    schedule_id=schedule.get("id", 0),
                    hour=schedule["hour"],
                    minute=schedule["minute"],
                    enabled=schedule.get("enabled", True),
                    weekdays_mask=schedule.get("weekdays", 0),
                    feed_options=proto.encode_feed_options(
                        tray_compartment_index=options.get("trayCompartmentIndex", 1),
                        feed_duration_seconds=options.get("feedDurationSeconds", 300),
                        heat_before_feeding=options.get("heatBeforeFeeding", False),
                        expected_heating_temperature=options.get(
                            "expectedHeatingTemperature", 24
                        ),
                        operator=self.client.user_id or 0,
                    ),
                )
            )
        arg = proto.encode_feeding_schedule_list(encoded)
        await self._async_send_command(
            {
                "kind": "invoke",
                "device_id": device_id,
                "cluster": proto.CLUSTER_FEED,
                "service": service,
                "arg": arg,
            }
        )
        await self.async_request_refresh()

    async def async_remove_schedules(self, device_id: str, schedule_ids: list[int]) -> None:
        await self._async_send_command(
            {
                "kind": "invoke",
                "device_id": device_id,
                "cluster": proto.CLUSTER_FEED,
                "service": proto.SERVICE_REMOVE_SCHEDULES,
                "arg": proto.encode_uint8_list(schedule_ids),
            }
        )
        await self.async_request_refresh()
