"""Minimal protobuf + UCSP thing-model codec for the Robotail RFeeder.

No external dependencies; implements just enough of the proto3 wire format
(varint + length-delimited fields) to build and parse the feeder messages.

All message layouts were reverse-engineered from the R Feeder app; see
docs/PROTOCOL.md for the full documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Generic protobuf wire helpers
# ---------------------------------------------------------------------------


def encode_varint(value: int) -> bytes:
    if value < 0:
        # two's complement 64 bit for negative ints
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field_varint(number: int, value: int, *, omit_zero: bool = True) -> bytes:
    if omit_zero and value == 0:
        return b""
    return encode_varint((number << 3) | 0) + encode_varint(value)


def field_bool(number: int, value: bool) -> bytes:
    return field_varint(number, 1 if value else 0)


def field_bytes(number: int, value: bytes, *, omit_empty: bool = True) -> bytes:
    if omit_empty and not value:
        return b""
    return encode_varint((number << 3) | 2) + encode_varint(len(value)) + value


def field_string(number: int, value: str, *, omit_empty: bool = True) -> bytes:
    return field_bytes(number, value.encode("utf-8"), omit_empty=omit_empty)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset
        shift += 7
        if shift >= 70:
            raise ValueError("protobuf varint is too long")


def decode_fields(data: bytes) -> list[tuple[int, int, Any]]:
    """Decode a protobuf buffer into a list of (field_number, wire_type, value)."""
    fields: list[tuple[int, int, Any]] = []
    offset = 0
    while offset < len(data):
        tag, offset = decode_varint(data, offset)
        number, wire_type = tag >> 3, tag & 0x07
        if wire_type == 0:
            value, offset = decode_varint(data, offset)
        elif wire_type == 2:
            length, offset = decode_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 1:
            value = int.from_bytes(data[offset : offset + 8], "little")
            offset += 8
        elif wire_type == 5:
            value = int.from_bytes(data[offset : offset + 4], "little")
            offset += 4
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        fields.append((number, wire_type, value))
    return fields


def first_varint(fields: list[tuple[int, int, Any]], number: int, default: int = 0) -> int:
    for num, wire, value in fields:
        if num == number and wire == 0:
            return value
    return default


def first_bytes(fields: list[tuple[int, int, Any]], number: int) -> bytes | None:
    for num, wire, value in fields:
        if num == number and wire == 2:
            return value
    return None


def first_string(fields: list[tuple[int, int, Any]], number: int) -> str | None:
    raw = first_bytes(fields, number)
    return raw.decode("utf-8", "replace") if raw is not None else None


def packed_varints(raw: bytes) -> list[int]:
    values: list[int] = []
    offset = 0
    while offset < len(raw):
        value, offset = decode_varint(raw, offset)
        values.append(value)
    return values


# ---------------------------------------------------------------------------
# Thing-model constants
# ---------------------------------------------------------------------------

ENDPOINT_MAIN = 10

CLUSTER_DEVICE = 10
CLUSTER_FEED = 11
CLUSTER_DIAGNOSIS = 12
CLUSTER_TESTING = 13

# FeedCluster attribute ids
ATTR_GLOBAL_STATE = 1
ATTR_FEED_STATE = 2
ATTR_DOOR_STATE = 3
ATTR_TRAY_STATE = 4
ATTR_TRAY_COMPARTMENT_COUNT = 5
ATTR_TRAY_FEEDABLE_COMPARTMENT = 6
ATTR_PREFERENCES = 7
ATTR_PET_DETECTION_ON = 8
ATTR_PET_DETECTED = 9
ATTR_TEMPERATURE_CONTROL_ON = 10
ATTR_ACTUAL_TEMPERATURE = 11
ATTR_EXPECTED_COOLING_TEMPERATURE = 12
ATTR_FEEDING_SCHEDULES = 13
ATTR_EXECUTING_FEEDING_SCHEDULE = 14
ATTR_CANCELLED_FEEDING_SCHEDULE = 15
ATTR_TEMPERATURE_SENSOR_FAULT = 16
ATTR_AUTO_FEEDING_ENABLED = 17
ATTR_TEMPERATURE_TREND = 18
ATTR_THERMOELECTRIC_TEMPERATURE = 19
ATTR_TRAY_TEMPERATURE = 20
ATTR_LATEST_EXECUTED_SCHEDULES = 21
ATTR_HEAT_BEFORE_FEED_NOW = 22
ATTR_PREHEAT_DURATION_MINUTES = 23

# FeedCluster service ids
SERVICE_FEED = 2
SERVICE_SKIP_HEATING = 3
SERVICE_STOP_FEEDING = 4
SERVICE_ADD_SCHEDULES = 7
SERVICE_REMOVE_SCHEDULES = 8
SERVICE_UPDATE_SCHEDULES = 9

OPCODE_STATUS_RESPONSE = 0
OPCODE_REPORT_DATA = 1
OPCODE_WRITE_REQUEST = 2
OPCODE_WRITE_RESPONSE = 3
OPCODE_INVOKE_REQUEST = 4
OPCODE_INVOKE_RESPONSE = 5
OPCODE_READ_REQUEST = 6
OPCODE_READ_RESPONSE = 7

MODEL_VERSION = 25

GLOBAL_STATES = {
    0: "RESETTING",
    1: "IDLE",
    2: "PAIRING",
    3: "FEEDING",
    4: "UPDATING",
    5: "TESTING",
    6: "ERASING",
}
FEED_STATES = {
    0: "STOPPED",
    1: "PREPARING",
    2: "STARTING",
    3: "SERVING",
    4: "STOPPING",
    5: "FAULT",
}
DOOR_STATES = {0: "CLOSED", 1: "OPENING", 2: "OPENED", 3: "CLOSING", 4: "FAULT"}
TRAY_STATES = {0: "NO_TRAY", 1: "ALIGNED", 2: "MISALIGNED", 3: "ROTATING", 4: "FAULT"}
POWER_SUPPLIES = {0: "BATTERY", 1: "DC"}
UPDATE_STATES = {
    0: "IDLE",
    1: "VALIDATING_FIRMWARE",
    2: "CHECK_PRECONDITION",
    3: "ABORTED",
    4: "UPDATING",
    5: "COMPLETED",
}
TRANSFER_STATES = {0: "IDLE", 1: "TRANSFERRING", 2: "STOPPED", 3: "ABORTED", 4: "COMPLETED"}
TEMPERATURE_TRENDS = {0: "STABILIZING", 1: "COOLING_DOWN", 2: "HEATING_UP"}
TEMPERATURE_SENSOR_FAULTS = {0: "NONE", 1: "OPEN_CIRCUIT", 2: "SHORT_CIRCUIT"}
FEEDING_ACTION_SOURCES = {0: "PHYSICAL_BUTTON", 1: "APP_REQUEST", 2: "SCHEDULE", 3: "AUTO_FEED"}
FEEDING_RECORD_EVENT_TYPES = {
    0: "RESTART",
    1: "END",
    2: "INTERRUPTED",
    3: "FAULT",
    4: "POWER_SUPPLY_BATTERY",
    5: "PREPARING",
    6: "STARTING",
    7: "DOOR_OPEN",
    8: "PET_DETECTED",
    9: "TRAY_ALIGNING",
    10: "DOOR_FAULT",
    11: "TRAY_FAULT",
    12: "TIMEOUT",
}
SCHEDULE_CANCELED_CAUSES = {0: "ILLEGAL_STATE", 1: "ALREADY_IN_FEEDING", 2: "TRAY_MISSING"}


def temp_to_wire(celsius: float) -> int:
    """Temperatures on the wire are half-degrees Celsius."""
    return int(round(celsius * 2))


def temp_from_wire(value: int) -> float:
    return value / 2


# ---------------------------------------------------------------------------
# Feeder message builders
# ---------------------------------------------------------------------------


def encode_feed_options(
    *,
    tray_compartment_index: int,
    feed_duration_seconds: int,
    heat_before_feeding: bool,
    expected_heating_temperature: float,
    operator: int,
) -> bytes:
    return b"".join(
        [
            field_varint(1, tray_compartment_index),
            field_varint(2, feed_duration_seconds),
            field_bool(3, heat_before_feeding),
            field_varint(4, temp_to_wire(expected_heating_temperature)),
            field_varint(5, operator),
        ]
    )


def encode_feed_preferences(
    *,
    feed_duration_seconds: int,
    heat_before_feeding: bool,
    expected_heating_temperature: float,
) -> bytes:
    return b"".join(
        [
            field_varint(1, feed_duration_seconds),
            field_bool(2, heat_before_feeding),
            field_varint(3, temp_to_wire(expected_heating_temperature)),
        ]
    )


def encode_time(hour: int, minute: int) -> bytes:
    return field_varint(1, hour) + field_varint(2, minute)


def encode_feeding_schedule(
    *,
    schedule_id: int,
    hour: int,
    minute: int,
    enabled: bool,
    weekdays_mask: int,
    feed_options: bytes,
) -> bytes:
    return b"".join(
        [
            field_varint(1, schedule_id),
            field_bytes(2, encode_time(hour, minute)),
            field_bool(3, enabled),
            field_varint(4, weekdays_mask),
            field_bytes(5, feed_options),
        ]
    )


def encode_feeding_schedule_list(schedules: list[bytes]) -> bytes:
    return b"".join(field_bytes(1, schedule) for schedule in schedules)


def encode_uint8_list(values: list[int]) -> bytes:
    packed = b"".join(encode_varint(v) for v in values)
    return field_bytes(1, packed, omit_empty=False)


def encode_primitive_data(value: int | bool) -> bytes:
    """Attribute data for primitives/enums: a single varint field 1."""
    if isinstance(value, bool):
        value = 1 if value else 0
    return field_varint(1, value, omit_zero=False)


# ---------------------------------------------------------------------------
# Interaction envelope
# ---------------------------------------------------------------------------


def encode_command_path(endpoint: int, cluster: int, command: int) -> bytes:
    return field_varint(1, endpoint) + field_varint(2, cluster) + field_varint(3, command)


def encode_attribute_path(endpoint: int, cluster: int, attribute: int) -> bytes:
    return encode_command_path(endpoint, cluster, attribute)


def encode_invoke_request(endpoint: int, cluster: int, command: int, arg: bytes) -> bytes:
    return field_bytes(1, encode_command_path(endpoint, cluster, command)) + field_bytes(2, arg, omit_empty=False)


def encode_write_request(endpoint: int, cluster: int, attributes: dict[int, bytes]) -> bytes:
    out = b""
    for attribute, data in attributes.items():
        out += field_bytes(
            1,
            field_bytes(1, encode_attribute_path(endpoint, cluster, attribute))
            + field_bytes(2, data, omit_empty=False),
        )
    return out


def encode_interaction(
    *,
    message_id: int,
    exchange_id: int,
    source_node_id: str,
    destination_node_id: str,
    timestamp_ms: int,
    opcode: int,
    payload: bytes,
) -> bytes:
    return b"".join(
        [
            field_varint(1, MODEL_VERSION),
            field_varint(2, message_id),
            field_varint(3, exchange_id),
            field_string(4, source_node_id),
            field_string(5, destination_node_id),
            field_varint(6, timestamp_ms),
            field_varint(7, opcode),
            field_bytes(8, payload, omit_empty=False),
        ]
    )


@dataclass(slots=True)
class Interaction:
    model_version: int
    message_id: int
    exchange_id: int
    source_node_id: str
    destination_node_id: str
    timestamp_ms: int
    opcode: int
    payload: bytes


def decode_interaction(raw: bytes) -> Interaction:
    fields = decode_fields(raw)
    return Interaction(
        model_version=first_varint(fields, 1),
        message_id=first_varint(fields, 2),
        exchange_id=first_varint(fields, 3),
        source_node_id=first_string(fields, 4) or "",
        destination_node_id=first_string(fields, 5) or "",
        timestamp_ms=first_varint(fields, 6),
        opcode=first_varint(fields, 7),
        payload=first_bytes(fields, 8) or b"",
    )


@dataclass(slots=True)
class AttributeReport:
    endpoint: int
    cluster: int
    attribute: int
    data: bytes


@dataclass(slots=True)
class EventReport:
    endpoint: int
    cluster: int
    event: int
    timestamp_ms: int
    data: bytes


def _decode_path(raw: bytes) -> tuple[int, int, int]:
    fields = decode_fields(raw)
    return (first_varint(fields, 1), first_varint(fields, 2), first_varint(fields, 3))


def decode_report_data(payload: bytes) -> tuple[list[AttributeReport], list[EventReport]]:
    fields = decode_fields(payload)
    attributes: list[AttributeReport] = []
    events: list[EventReport] = []
    for num, wire, value in fields:
        if wire != 2:
            continue
        sub = decode_fields(value)
        path_raw = first_bytes(sub, 1)
        if path_raw is None:
            continue
        endpoint, cluster, item = _decode_path(path_raw)
        if num == 1:
            attributes.append(AttributeReport(endpoint, cluster, item, first_bytes(sub, 2) or b""))
        elif num == 2:
            events.append(
                EventReport(endpoint, cluster, item, first_varint(sub, 2), first_bytes(sub, 3) or b"")
            )
    return attributes, events


def decode_invoke_response(payload: bytes) -> tuple[int, int, int, int]:
    """Return (endpoint, cluster, command, status)."""
    fields = decode_fields(payload)
    path_raw = first_bytes(fields, 1)
    if path_raw is None:
        return (0, 0, 0, first_varint(fields, 2))
    endpoint, cluster, command = _decode_path(path_raw)
    return (endpoint, cluster, command, first_varint(fields, 2))


def decode_primitive_data(data: bytes) -> int:
    """Decode attribute data of primitives/enums (single varint field 1)."""
    if not data:
        return 0
    fields = decode_fields(data)
    return first_varint(fields, 1)


def decode_feed_preferences(data: bytes) -> dict[str, Any]:
    fields = decode_fields(data)
    return {
        "feedDurationSeconds": first_varint(fields, 1),
        "heatBeforeFeeding": bool(first_varint(fields, 2)),
        "expectedHeatingTemperature": temp_from_wire(first_varint(fields, 3)),
    }


def decode_feeding_schedule(data: bytes) -> dict[str, Any]:
    fields = decode_fields(data)
    schedule: dict[str, Any] = {
        "id": first_varint(fields, 1),
        "enabled": bool(first_varint(fields, 3)),
        "weekdays": first_varint(fields, 4),
    }
    start_raw = first_bytes(fields, 2)
    if start_raw:
        start_fields = decode_fields(start_raw)
        schedule["hour"] = first_varint(start_fields, 1)
        schedule["minute"] = first_varint(start_fields, 2)
    options_raw = first_bytes(fields, 5)
    if options_raw:
        schedule["feedOptions"] = decode_feed_options(options_raw)
    return schedule


def decode_feed_options(data: bytes) -> dict[str, Any]:
    fields = decode_fields(data)
    return {
        "trayCompartmentIndex": first_varint(fields, 1),
        "feedDurationSeconds": first_varint(fields, 2),
        "heatBeforeFeeding": bool(first_varint(fields, 3)),
        "expectedHeatingTemperature": temp_from_wire(first_varint(fields, 4)),
        "operator": first_varint(fields, 5),
    }


def decode_feeding_schedule_list(data: bytes) -> list[dict[str, Any]]:
    fields = decode_fields(data)
    return [decode_feeding_schedule(value) for num, wire, value in fields if num == 1 and wire == 2]
