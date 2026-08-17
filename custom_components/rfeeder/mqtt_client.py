"""Minimal blocking MQTT 3.1.1 client over TLS (stdlib only).

Implements just enough for the UCSP broker: CONNECT/SUBSCRIBE/PUBLISH QoS 0+1,
PINGREQ, and a read loop that yields published messages. All methods are
blocking and meant to run in an executor / dedicated thread.
"""

from __future__ import annotations

import socket
import ssl
import time
from typing import Any

KEEPALIVE_SECONDS = 60


class MqttError(RuntimeError):
    pass


def _remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length:
            digit |= 0x80
        out.append(digit)
        if not length:
            return bytes(out)


def _utf8(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) > 0xFFFF:
        raise ValueError("MQTT string too long")
    return len(encoded).to_bytes(2, "big") + encoded


def _packet(first_byte: int, payload: bytes) -> bytes:
    return bytes([first_byte]) + _remaining_length(len(payload)) + payload


def _recv_exact(conn: socket.socket, length: int) -> bytes:
    out = bytearray()
    while len(out) < length:
        chunk = conn.recv(length - len(out))
        if not chunk:
            raise MqttError("MQTT socket closed")
        out += chunk
    return bytes(out)


def read_packet(conn: socket.socket, timeout: float) -> tuple[int, int, bytes] | None:
    """Read one MQTT packet; returns None on timeout."""
    conn.settimeout(max(timeout, 0.001))
    try:
        first = conn.recv(1)
    except socket.timeout:
        return None
    except ssl.SSLError as err:
        if "timed out" in str(err):
            return None
        raise
    if not first:
        raise MqttError("MQTT socket closed")
    multiplier = 1
    remaining = 0
    while True:
        digit = _recv_exact(conn, 1)[0]
        remaining += (digit & 0x7F) * multiplier
        if not (digit & 0x80):
            break
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise MqttError("malformed remaining length")
    payload = _recv_exact(conn, remaining) if remaining else b""
    return first[0] >> 4, first[0] & 0x0F, payload


def parse_publish(flags: int, payload: bytes) -> tuple[str, int, bytes, int | None]:
    if len(payload) < 2:
        raise MqttError("malformed PUBLISH")
    topic_len = int.from_bytes(payload[:2], "big")
    topic = payload[2 : 2 + topic_len].decode("utf-8", "replace")
    qos = (flags >> 1) & 0x03
    offset = 2 + topic_len
    packet_id = None
    if qos:
        packet_id = int.from_bytes(payload[offset : offset + 2], "big")
        offset += 2
    return topic, qos, payload[offset:], packet_id


class UcspMqttClient:
    """Blocking MQTT-over-TLS client for the UCSP broker."""

    def __init__(self, host: str, port: int, *, timeout: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._conn: socket.socket | None = None
        self._packet_id = 0
        self._last_io = 0.0

    # -- connection handling -------------------------------------------------

    def connect(self, *, client_id: str, username: str, password: str) -> None:
        self.close()
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        context = ssl.create_default_context()
        conn = context.wrap_socket(raw, server_hostname=self.host)
        variable_header = b"".join(
            [
                _utf8("MQTT"),
                b"\x04",  # protocol level 3.1.1
                b"\xc2",  # clean session + username + password
                KEEPALIVE_SECONDS.to_bytes(2, "big"),
            ]
        )
        payload = variable_header + _utf8(client_id) + _utf8(username) + _utf8(password)
        conn.sendall(_packet(0x10, payload))
        packet = read_packet(conn, self.timeout)
        if packet is None or packet[0] != 2 or packet[2] != b"\x00\x00":
            conn.close()
            raise MqttError(f"MQTT CONNECT rejected: {packet!r}")
        self._conn = conn
        self._last_io = time.monotonic()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.sendall(_packet(0xE0, b""))
            except OSError:
                pass
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # -- protocol operations -------------------------------------------------

    def _next_packet_id(self) -> int:
        self._packet_id = (self._packet_id + 1) & 0xFFFF
        return self._packet_id or 1

    def subscribe(self, topic: str, qos: int = 1) -> None:
        self._require_conn()
        packet_id = self._next_packet_id()
        payload = packet_id.to_bytes(2, "big") + _utf8(topic) + bytes([qos])
        self._conn.sendall(_packet(0x82, payload))
        self._last_io = time.monotonic()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            packet = read_packet(self._conn, deadline - time.monotonic())
            if packet is None:
                continue
            if packet[0] == 9:
                return
            self._handle_packet(packet)
        raise MqttError("MQTT SUBSCRIBE timed out")

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        self._require_conn()
        body = _utf8(topic)
        packet_id = 0
        if qos:
            packet_id = self._next_packet_id()
            body += packet_id.to_bytes(2, "big")
        self._conn.sendall(_packet(0x30 | (qos << 1), body + payload))
        self._last_io = time.monotonic()

    def read_message(self, timeout: float) -> tuple[str, bytes] | None:
        """Wait for the next PUBLISH message; returns (topic, payload) or None on timeout.

        Handles PUBACK/SUBACK/PINGRESP transparently and keeps the connection
        alive with PINGREQ.
        """
        self._require_conn()
        deadline = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            if now >= deadline:
                return None
            if now - self._last_io > KEEPALIVE_SECONDS / 2:
                self._conn.sendall(_packet(0xC0, b""))
                self._last_io = now
            packet = read_packet(self._conn, min(deadline - now, 1.0))
            if packet is None:
                continue
            message = self._handle_packet(packet)
            if message is not None:
                return message

    def _handle_packet(self, packet: tuple[int, int, bytes]) -> tuple[str, bytes] | None:
        packet_type, flags, payload = packet
        if packet_type == 3:  # PUBLISH
            topic, qos, message_payload, packet_id = parse_publish(flags, payload)
            if qos and packet_id is not None:
                self._conn.sendall(_packet(0x40, packet_id.to_bytes(2, "big")))
            self._last_io = time.monotonic()
            return topic, message_payload
        if packet_type == 13:  # PINGRESP
            self._last_io = time.monotonic()
        return None

    def _require_conn(self) -> None:
        if self._conn is None:
            raise MqttError("MQTT client is not connected")
