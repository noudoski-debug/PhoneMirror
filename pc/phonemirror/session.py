"""Connection loop: find the iPhone, connect, receive video, send touches. No GUI dependencies."""
import socket
import threading
import time

from . import protocol as P
from . import usbmux


def usb_connect_fn(port=P.DEVICE_PORT):
    """Connect to the first USB-attached device."""
    devices = [d for d in usbmux.list_devices() if d["connection"] in ("USB", "")]
    if not devices:
        raise usbmux.UsbmuxError("no iPhone found (cable, trust prompt, Apple Devices/iTunes installed?)")
    return usbmux.connect(devices[0]["id"], port)


class Session:
    def __init__(self, connect_fn, decoder_factory, on_frame, on_status=lambda s: None, retry_delay=1.5):
        self._connect_fn = connect_fn
        self._decoder_factory = decoder_factory
        self._on_frame = on_frame
        self._on_status = on_status
        self._retry_delay = retry_delay
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._sock = None
        self._thread = threading.Thread(target=self._run, name="session", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=3)

    @property
    def connected(self):
        return self._sock is not None

    def _send(self, data):
        sock = self._sock
        if sock is None:
            return False
        try:
            with self._send_lock:
                sock.sendall(data)
            return True
        except OSError:
            return False

    def send_touch(self, action, finger, x, y):
        return self._send(P.pack_touch(action, finger, x, y))

    def request_keyframe(self):
        return self._send(P.pack_message(P.MSG_KEYFRAME_REQ))

    def _run(self):
        while not self._stop.is_set():
            self._on_status("searching for iPhone...")
            try:
                sock = self._connect_fn()
            except Exception as e:  # noqa: BLE001 - any failure just means "retry"
                self._on_status(f"waiting: {e}")
                self._stop.wait(self._retry_delay)
                continue
            self._sock = sock
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            self._on_status("connected, waiting for video (start the broadcast on the phone)")
            try:
                self._receive_loop(sock)
            except (ConnectionError, OSError, P.ProtocolError) as e:
                if not self._stop.is_set():
                    self._on_status(f"disconnected: {e}")
            finally:
                self._sock = None
                try:
                    sock.close()
                except OSError:
                    pass
            self._stop.wait(self._retry_delay)

    def _receive_loop(self, sock):
        decoder = self._decoder_factory()
        self.request_keyframe()
        while not self._stop.is_set():
            msg_type, payload = P.read_message(sock)
            if msg_type == P.MSG_HELLO:
                version = P.unpack_hello(payload)
                if version != P.PROTOCOL_VERSION:
                    raise P.ProtocolError(f"protocol version mismatch (device {version}, pc {P.PROTOCOL_VERSION})")
                self._on_status("streaming")
            elif msg_type == P.MSG_VIDEO:
                orientation, _key, data = P.unpack_video(payload)
                try:
                    frames = decoder.decode(data)
                except Exception:  # noqa: BLE001 - corrupt stream: resync on next keyframe
                    decoder = self._decoder_factory()
                    self.request_keyframe()
                    continue
                for frame in frames:
                    self._on_frame(frame, orientation)
            # unknown message types are ignored for forward compatibility
