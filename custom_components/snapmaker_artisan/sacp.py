"""SACP protocol helpers for talking to a Snapmaker Artisan.

Packet framing (make_packet/read_packet) and the checksum functions are
verified byte-for-byte against real packet captures. Subscription
targets (heartbeat 0x01/0xA0, fan 0x10/0xA3, elapsed time 0xAC/0xA5,
print line 0xAC/0xA0, extruders 0x10/0xA0, bed 0x14/0xA0, enclosure
0x15/0xA0) and their decoders were confirmed against real packet
captures and/or live correlated testing (toggling things on the
touchscreen and watching which bytes moved).

Position (0x01/0x30) is NOT a subscription -- the subscribe wrapper is
CONFIRMED REJECTED on both receiver=1 and receiver=2. Instead it's a
direct-addressed one-shot query (command_set/command_id ARE 0x01/0x30,
not wrapped in 0x01/0x00) that must be re-sent on a timer. CONFIRMED
WORKING -- returns real X/Y/Z and a homed flag.

Device info (0x01/0x20) is also a direct one-shot query, confirmed
working. Module 1024's firmware blob decodes into a table of
submodules.

Enclosure (0x15/0xA0) byte mapping confirmed via live testing on
2026-10-04: see decode_enclosure().

UDP discovery (port 20054, plaintext "discover" message, pipe-delimited
reply) matches the protocol used by Snapmaker Luban and several
community tools.
"""

from __future__ import annotations

import asyncio
import re
import socket
import struct
from dataclasses import dataclass, field
from typing import Any

from .const import STATUS_NAMES

SACP_MAGIC = b"\xAA\x55"


# ---------------------------------------------------------------------------
# Checksums / framing
# ---------------------------------------------------------------------------

def head_checksum(data: bytes) -> int:
    crc = 0
    poly = 7
    for byte in data:
        for j in range(8):
            bit = ((byte >> (7 - j)) & 1) == 1
            c07 = ((crc >> 7) & 1) == 1
            crc = (crc << 1) & 0xFF
            if (not c07 and bit) or (c07 and not bit):
                crc ^= poly
    return crc & 0xFF


def data_checksum(data: bytes) -> int:
    check = 0
    i = 0
    while i < len(data) - 1:
        check += (data[i] << 8) | data[i + 1]
        check &= 0xFFFFFFFF
        i += 2
    if len(data) % 2:
        check += data[-1]
    while check > 0xFFFF:
        check = ((check >> 16) & 0xFFFF) + (check & 0xFFFF)
    return (~check) & 0xFFFF


def make_packet(receiver, sender, attribute, sequence, command_set, command_id, payload=b""):
    packet = bytearray(15 + len(payload))
    packet[0:2] = SACP_MAGIC
    packet[2:4] = struct.pack("<H", len(payload) + 8)
    packet[4] = 0x01
    packet[5] = receiver
    packet[6] = head_checksum(packet[:6])
    packet[7] = sender
    packet[8] = attribute
    packet[9:11] = struct.pack("<H", sequence)
    packet[11] = command_set
    packet[12] = command_id
    packet[13:13 + len(payload)] = payload
    packet[-2:] = struct.pack("<H", data_checksum(packet[7:-2]))
    return bytes(packet)


async def read_packet(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(4)
    if header[:2] != SACP_MAGIC:
        raise ValueError(f"Invalid SACP header: {header.hex(' ')}")

    data_length = struct.unpack("<H", header[2:4])[0]
    body = await reader.readexactly(data_length + 3)
    packet = header + body

    if packet[4] != 0x01:
        raise ValueError("Unsupported SACP version")
    if head_checksum(packet[:6]) != packet[6]:
        raise ValueError("Bad SACP header checksum")
    if data_checksum(packet[7:-2]) != struct.unpack("<H", packet[-2:])[0]:
        raise ValueError("Bad SACP data checksum")

    return {
        "receiver": packet[5],
        "sender": packet[7],
        "attribute": packet[8],
        "sequence": struct.unpack("<H", packet[9:11])[0],
        "command_set": packet[11],
        "command_id": packet[12],
        "payload": packet[13:-2],
    }


CLIENT_NAME = b"Home Assistant"


async def handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, timeout: float) -> None:
    payload = struct.pack("<H", len(CLIENT_NAME)) + CLIENT_NAME + struct.pack("<HH", 0, 0)
    writer.write(make_packet(receiver=2, sender=0, attribute=0, sequence=1,
                              command_set=0x01, command_id=0x05, payload=payload))
    await writer.drain()

    async def _wait():
        while True:
            response = await read_packet(reader)
            if response["command_set"] == 0x01 and response["command_id"] == 0x05:
                return

    await asyncio.wait_for(_wait(), timeout=timeout)


async def subscribe(reader, writer, sequence, receiver, target_cs, target_cid, interval_ms, timeout):
    """Returns the ack status byte: 0 = accepted, anything else = rejected."""
    payload = bytes([target_cs, target_cid]) + struct.pack("<H", interval_ms)
    writer.write(make_packet(receiver=receiver, sender=0, attribute=0, sequence=sequence,
                              command_set=0x01, command_id=0x00, payload=payload))
    await writer.drain()

    async def _wait():
        while True:
            response = await read_packet(reader)
            if (response["sequence"] == sequence and response["command_set"] == 0x01
                    and response["command_id"] == 0x00):
                return response["payload"][0] if response["payload"] else None

    try:
        return await asyncio.wait_for(_wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def make_position_query_packet(sequence: int, interval_ms: int = 1000) -> bytes:
    """Direct-addressed one-shot query -- NOT the subscribe wrapper.
    CONFIRMED WORKING. Must be re-sent on a timer; there's no ongoing
    subscription to this."""
    payload = struct.pack("<H", interval_ms)
    return make_packet(receiver=1, sender=0, attribute=0, sequence=sequence,
                        command_set=0x01, command_id=0x30, payload=payload)


def make_disconnect_packet() -> bytes:
    return make_packet(receiver=2, sender=0, attribute=0, sequence=1,
                        command_set=0x01, command_id=0x06, payload=b"")


# ---------------------------------------------------------------------------
# Device info -- one-shot direct query (0x01/0x20). Confirmed working.
# ---------------------------------------------------------------------------

def make_module_info_query_packet(sequence: int) -> bytes:
    return make_packet(receiver=1, sender=0, attribute=0, sequence=sequence,
                        command_set=0x01, command_id=0x20, payload=b"")


def parse_module_info_response(data: bytes) -> list[dict[str, Any]]:
    if len(data) < 1:
        raise RuntimeError("Module info response too short")
    if data[0] != 0:
        raise RuntimeError(f"Get Module Info List failed, result={data[0]}")

    offset = 1
    modules = []
    while offset < len(data):
        if offset + 1 > len(data):
            break
        key = data[offset]
        offset += 1
        if offset + 2 > len(data):
            break
        module_id = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        if offset + 1 > len(data):
            break
        module_index = data[offset]
        offset += 1
        if offset + 1 > len(data):
            break
        module_state = data[offset]
        offset += 1
        if offset + 4 > len(data):
            break
        serial_num = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if offset + 1 > len(data):
            break
        hardware_version = data[offset]
        offset += 1
        if offset + 2 > len(data):
            break
        fw_len = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        if offset + fw_len > len(data):
            firmware_bytes = data[offset:]
            offset = len(data)
        else:
            firmware_bytes = data[offset:offset + fw_len]
            offset += fw_len

        modules.append({
            "key": key,
            "module_id": module_id,
            "module_index": module_index,
            "module_state": module_state,
            "serial_num": serial_num,
            "hardware_version": hardware_version,
            "firmware_version": firmware_bytes.decode("ascii", errors="ignore"),
        })
    return modules


def parse_submodules(fw_string: str) -> list[dict[str, Any]]:
    """module_id==1024's firmware_version is a table of submodule
    entries, each ending in a 'vX.Y.Z' version preceded by its own
    2-byte little-endian length. Split on that; byte 0 of the gap
    between entries is the entry index, byte 1 a module-type code."""
    data = fw_string.encode("latin1", errors="replace")
    entries = []
    prev_end = 0
    for m in re.finditer(rb"v\d+\.\d+\.\d+", data):
        start = m.start()
        version = m.group(0)
        has_len = start >= 2 and struct.unpack_from("<H", data, start - 2)[0] == len(version)
        desc_end = start - 2 if has_len else start
        descriptor = data[prev_end:desc_end]
        entries.append({
            "index": descriptor[0] if len(descriptor) >= 1 else None,
            "type": descriptor[1] if len(descriptor) >= 2 else None,
            "descriptor": descriptor.hex(" "),
            "version": version.decode("ascii"),
        })
        prev_end = m.end()
    return entries


# Only type 0x10 is backed by evidence (same ID as telemetry command set
# 0x10, which carries this printer's extruder temps and fan data).
KNOWN_SUBMODULE_TYPES = {0x10: "print head"}


def find_print_head_firmware(modules: list[dict[str, Any]]) -> str | None:
    """Convenience for populating the HA device registry's sw_version."""
    for m in modules:
        if m["module_id"] != 1024:
            continue
        for sm in parse_submodules(m["firmware_version"]):
            if sm["type"] == 0x10:
                return sm["version"]
    return None


# ---------------------------------------------------------------------------
# Decoders -- each takes the raw payload bytes for its command_set/command_id
# ---------------------------------------------------------------------------

def decode_heartbeat(data: bytes):
    if len(data) < 2:
        return None
    return STATUS_NAMES.get(data[1], f"unknown_{data[1]}")


def decode_position(data: bytes):
    if len(data) < 5:
        return None
    homed = data[1] == 0
    axis_count = data[4]
    offset = 5
    positions = {}
    for _ in range(axis_count):
        if offset + 5 > len(data):
            break
        axis = data[offset]
        positions[axis] = struct.unpack("<i", data[offset + 1:offset + 5])[0] / 1000.0
        offset += 5
    return {"homed": homed, "x": positions.get(0), "y": positions.get(1), "z": positions.get(2)}


def decode_elapsed_seconds(data: bytes):
    if len(data) < 5:
        return None
    return struct.unpack("<I", data[1:5])[0]


def decode_print_line(data: bytes):
    if len(data) < 5:
        return None
    return struct.unpack("<I", data[1:5])[0]


def decode_fan(data: bytes):
    if len(data) < 3:
        return None
    count = data[2]
    fans = []
    offset = 3
    for _ in range(count):
        if offset + 3 > len(data):
            break
        fans.append({"id": data[offset], "percent": round(data[offset + 2] / 255 * 100)})
        offset += 3
    return fans or None


def decode_extruders(data: bytes):
    # CONFIRMED layout -- 17-byte records: [tool_id, 8 unknown bytes,
    # current(i32 millideg), target(i32 millideg)].
    if len(data) < 3:
        return None
    count = data[2]
    offset = 3
    tools = []
    for _ in range(count):
        if offset + 17 > len(data):
            break
        tool = data[offset]
        current = struct.unpack("<i", data[offset + 9:offset + 13])[0] / 1000
        target = struct.unpack("<i", data[offset + 13:offset + 17])[0] / 1000
        tools.append({"tool": tool, "current": current, "target": target})
        offset += 17
    return tools or None


def decode_bed(data: bytes):
    # CONFIRMED layout.
    if len(data) < 3:
        return None
    count = data[2]
    offset = 3
    zones = []
    for _ in range(count):
        if offset + 7 > len(data):
            break
        zone = data[offset]
        current = struct.unpack("<i", data[offset + 1:offset + 5])[0] / 1000
        target = struct.unpack("<h", data[offset + 5:offset + 7])[0]
        zones.append({"zone": zone, "current": current, "target": target})
        offset += 7
    return zones or None


def decode_enclosure(data: bytes):
    """0x15/0xA0, enclosure module. Confirmed by live testing on
    2026-10-04 (toggling things on the touchscreen and watching which
    byte moved):
      byte[6]  = door-detection enabled for 3D print jobs (0/1)
      byte[10] = door-detection enabled for CNC jobs (0/1)
      byte[11] = door open (0=closed, 1=open)
      byte[12] = enclosure fan speed, 0-100
    byte[3] is UNCONFIRMED -- it's a plausible candidate for light
    power (it's always 0x64/100, a sensible default-on value) but it
    was never isolated by actually toggling the light during testing,
    unlike the fields above. Exposed anyway since it's the best
    candidate available; treat it with real skepticism until someone
    toggles the light specifically and confirms it moves.
    byte[1] looks like a boot/session counter (unconfirmed). The rest
    (bytes[2], [4:6], [7:10] apart from the two flags above) are still
    unknown -- not guessing further than this.
    """
    if len(data) < 13:
        return None
    return {
        "door_open": bool(data[11]),
        "fan_percent": data[12],
        "door_detect_3dp": bool(data[6]),
        "door_detect_cnc": bool(data[10]),
        "light_percent_unconfirmed": data[3],
    }


# Each entry: (key, receiver, command_set, command_id, decoder)
# Position is deliberately NOT here -- it's a direct one-shot query
# handled separately (see make_position_query_packet), not a subscription.
SUBSCRIPTIONS: list[tuple[str, int, int, int, Any]] = [
    ("status", 1, 0x01, 0xA0, decode_heartbeat),
    ("elapsed_s", 1, 0xAC, 0xA5, decode_elapsed_seconds),
    ("print_line", 1, 0xAC, 0xA0, decode_print_line),
    ("fan", 1, 0x10, 0xA3, decode_fan),
    ("extruders", 1, 0x10, 0xA0, decode_extruders),
    ("bed", 1, 0x14, 0xA0, decode_bed),
    ("enclosure", 1, 0x15, 0xA0, decode_enclosure),
]


# ---------------------------------------------------------------------------
# UDP discovery (port 20054): send "discover", parse the pipe-delimited
# reply, e.g. "J1X123P@192.168.1.19|model:Snapmaker Artisan|status:IDLE|SACP:1"
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryInfo:
    name: str
    ip: str
    model: str | None = None
    status: str | None = None


def _discover_sync(host: str, timeout: float) -> DiscoveryInfo | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"discover", (host, 20054))
        data, _ = sock.recvfrom(4096)
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()

    try:
        msg = data.decode(errors="ignore").strip()
        parts = msg.split("|")
        name, ip = parts[0].split("@")
        info = DiscoveryInfo(name=name, ip=ip)
        for token in parts[1:]:
            if ":" not in token:
                continue
            key, value = token.split(":", 1)
            key = key.strip().lower()
            if key == "model":
                info.model = value
            elif key == "status":
                info.status = value
        return info
    except Exception:  # noqa: BLE001 - malformed reply, treat as no discovery
        return None


async def async_discover(hass, host: str, timeout: float = 3) -> DiscoveryInfo | None:
    return await hass.async_add_executor_job(_discover_sync, host, timeout)
