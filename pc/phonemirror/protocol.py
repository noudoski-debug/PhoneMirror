"""Wire protocol between the iPhone (Broadcast extension) and the PC.

Every message: [type: u8][length: u32 LE][payload]. See README for the layout.
"""
import struct

PROTOCOL_VERSION = 1
DEVICE_PORT = 8765

MSG_HELLO = 0x01          # device -> pc: u16 version
MSG_VIDEO = 0x02          # device -> pc: u8 orientation, u8 flags(bit0=keyframe), H.264 Annex B
MSG_TOUCH = 0x10          # pc -> device: u8 action, u8 finger, f32 x, f32 y
MSG_KEYFRAME_REQ = 0x11   # pc -> device: empty

TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP = 0, 1, 2

_HEADER = struct.Struct("<BI")
_TOUCH = struct.Struct("<BBff")
_VIDEO = struct.Struct("<BB")
MAX_PAYLOAD = 16 * 1024 * 1024


class ProtocolError(Exception):
    pass


def pack_message(msg_type, payload=b""):
    return _HEADER.pack(msg_type, len(payload)) + payload


def read_exact(sock, n):
    chunks = []
    remaining = n
    while remaining:
        chunk = sock.recv(min(remaining, 65536))
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(sock):
    msg_type, length = _HEADER.unpack(read_exact(sock, _HEADER.size))
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"payload too large: {length}")
    return msg_type, read_exact(sock, length) if length else b""


def pack_touch(action, finger, x, y):
    if action not in (TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP):
        raise ValueError("bad touch action")
    x = min(1.0, max(0.0, float(x)))
    y = min(1.0, max(0.0, float(y)))
    return pack_message(MSG_TOUCH, _TOUCH.pack(action, finger, x, y))


def unpack_touch(payload):
    if len(payload) != _TOUCH.size:
        raise ProtocolError("bad touch payload")
    return _TOUCH.unpack(payload)


def pack_hello(version=PROTOCOL_VERSION):
    return pack_message(MSG_HELLO, struct.pack("<H", version))


def unpack_hello(payload):
    if len(payload) != 2:
        raise ProtocolError("bad hello payload")
    return struct.unpack("<H", payload)[0]


def pack_video(orientation, keyframe, data):
    return pack_message(MSG_VIDEO, _VIDEO.pack(orientation, 1 if keyframe else 0) + data)


def unpack_video(payload):
    if len(payload) < _VIDEO.size:
        raise ProtocolError("bad video payload")
    orientation, flags = _VIDEO.unpack_from(payload)
    return orientation, bool(flags & 1), payload[_VIDEO.size:]
