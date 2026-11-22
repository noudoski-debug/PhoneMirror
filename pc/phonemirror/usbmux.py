"""Minimal usbmuxd client (plist protocol) to open a TCP tunnel to a port on a USB-connected iPhone.

On Windows usbmuxd is provided by Apple Mobile Device Service (iTunes / Apple Devices app)
and listens on 127.0.0.1:27015. On macOS/Linux it is a unix socket.
"""
import plistlib
import socket
import struct
import sys

_HDR = struct.Struct("<IIII")  # length, version, request, tag
_VERSION_PLIST = 1
_REQUEST_PLIST = 8
_PROG = "phonemirror"


class UsbmuxError(Exception):
    pass


def _open_mux(address=None):
    if address is not None:
        if isinstance(address, str):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(address)
        return s
    if sys.platform == "win32":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(("127.0.0.1", 27015))
    else:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect("/var/run/usbmuxd")
    return s


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise UsbmuxError("usbmuxd closed the connection")
        buf += chunk
    return buf


def _send(sock, payload, tag=1):
    data = plistlib.dumps(payload)
    sock.sendall(_HDR.pack(_HDR.size + len(data), _VERSION_PLIST, _REQUEST_PLIST, tag) + data)


def _recv(sock):
    length, _version, _request, _tag = _HDR.unpack(_recv_exact(sock, _HDR.size))
    if length < _HDR.size or length > 1 << 22:
        raise UsbmuxError("bad usbmuxd frame")
    return plistlib.loads(_recv_exact(sock, length - _HDR.size))


def list_devices(address=None):
    """Return [{'id': int, 'serial': str, 'connection': str}] for attached devices."""
    sock = _open_mux(address)
    try:
        _send(sock, {"MessageType": "ListDevices", "ClientVersionString": _PROG, "ProgName": _PROG})
        reply = _recv(sock)
    finally:
        sock.close()
    devices = []
    for entry in reply.get("DeviceList", []):
        props = entry.get("Properties", {})
        devices.append({
            "id": entry.get("DeviceID", props.get("DeviceID")),
            "serial": props.get("SerialNumber", ""),
            "connection": props.get("ConnectionType", ""),
        })
    return devices


def connect(device_id, port, address=None):
    """Open a raw TCP tunnel to `port` on the device. Returns the connected socket."""
    sock = _open_mux(address)
    try:
        _send(sock, {
            "MessageType": "Connect",
            "ClientVersionString": _PROG,
            "ProgName": _PROG,
            "DeviceID": device_id,
            "PortNumber": socket.htons(port),
        })
        reply = _recv(sock)
        code = reply.get("Number", -1)
        if code != 0:
            hint = {2: "device not found", 3: "connection refused (is the broadcast running?)"}.get(code, "unknown error")
            raise UsbmuxError(f"usbmuxd connect failed ({code}): {hint}")
    except Exception:
        sock.close()
        raise
    sock.settimeout(None)
    return sock
