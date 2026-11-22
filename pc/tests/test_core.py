import os
import plistlib
import socket
import struct
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from phonemirror import protocol as P
from phonemirror import usbmux, geometry, mapping


class ProtocolTests(unittest.TestCase):
    def pair(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, b

    def test_touch_roundtrip_and_clamp(self):
        a, b = self.pair()
        a.sendall(P.pack_touch(P.TOUCH_MOVE, 3, 1.5, -0.2))
        t, payload = P.read_message(b)
        self.assertEqual(t, P.MSG_TOUCH)
        action, finger, x, y = P.unpack_touch(payload)
        self.assertEqual((action, finger), (P.TOUCH_MOVE, 3))
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 0.0)

    def test_video_roundtrip(self):
        a, b = self.pair()
        a.sendall(P.pack_video(6, True, b"\x00\x00\x00\x01abc"))
        t, payload = P.read_message(b)
        self.assertEqual(P.unpack_video(payload), (6, True, b"\x00\x00\x00\x01abc"))

    def test_hello_and_empty_payload(self):
        a, b = self.pair()
        a.sendall(P.pack_hello() + P.pack_message(P.MSG_KEYFRAME_REQ))
        t, payload = P.read_message(b)
        self.assertEqual(P.unpack_hello(payload), P.PROTOCOL_VERSION)
        self.assertEqual(P.read_message(b), (P.MSG_KEYFRAME_REQ, b""))

    def test_rejects_oversized_and_bad_touch(self):
        a, b = self.pair()
        a.sendall(struct.pack("<BI", P.MSG_VIDEO, P.MAX_PAYLOAD + 1))
        with self.assertRaises(P.ProtocolError):
            P.read_message(b)
        with self.assertRaises(P.ProtocolError):
            P.unpack_touch(b"123")
        with self.assertRaises(ValueError):
            P.pack_touch(9, 0, 0, 0)

    def test_closed_connection(self):
        a, b = self.pair()
        a.close()
        with self.assertRaises(ConnectionError):
            P.read_message(b)


class FakeUsbmuxd(threading.Thread):
    """Speaks just enough usbmuxd to test the client."""

    def __init__(self, connect_result=0):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.address = self.srv.getsockname()
        self.connect_result = connect_result
        self.seen = []

    def run(self):
        for _ in range(2):
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            hdr = usbmux._recv_exact(conn, 16)
            length, version, request, tag = struct.unpack("<IIII", hdr)
            assert (version, request) == (1, 8)
            msg = plistlib.loads(usbmux._recv_exact(conn, length - 16))
            self.seen.append(msg)
            if msg["MessageType"] == "ListDevices":
                reply = {"DeviceList": [{"DeviceID": 7, "MessageType": "Attached", "Properties": {
                    "DeviceID": 7, "SerialNumber": "ABC", "ConnectionType": "USB"}}]}
            else:
                reply = {"MessageType": "Result", "Number": self.connect_result}
            data = plistlib.dumps(reply)
            conn.sendall(struct.pack("<IIII", 16 + len(data), 1, 8, tag) + data)
            if msg["MessageType"] == "ListDevices":
                conn.close()
            else:
                self.tunnel = conn

    def close(self):
        self.srv.close()


class UsbmuxTests(unittest.TestCase):
    def test_list_and_connect(self):
        fake = FakeUsbmuxd()
        fake.start()
        self.addCleanup(fake.close)
        devices = usbmux.list_devices(fake.address)
        self.assertEqual(devices, [{"id": 7, "serial": "ABC", "connection": "USB"}])
        sock = usbmux.connect(7, 8765, fake.address)
        self.addCleanup(sock.close)
        fake.join(2)
        self.assertEqual(fake.seen[1]["DeviceID"], 7)
        self.assertEqual(fake.seen[1]["PortNumber"], socket.htons(8765))
        fake.tunnel.sendall(b"raw")           # after Connect the socket is a raw tunnel
        self.assertEqual(sock.recv(3), b"raw")

    def test_connect_refused(self):
        fake = FakeUsbmuxd(connect_result=3)
        fake.start()
        self.addCleanup(fake.close)
        usbmux.list_devices(fake.address)
        with self.assertRaises(usbmux.UsbmuxError) as ctx:
            usbmux.connect(7, 8765, fake.address)
        self.assertIn("broadcast", str(ctx.exception))


class GeometryTests(unittest.TestCase):
    def test_view_portrait_roundtrip(self):
        for rot in (0, 90, 180, 270):
            for (x, y) in [(0.1, 0.2), (0.9, 0.3), (0.5, 0.5)]:
                u, v = geometry.portrait_to_view(x, y, rot)
                x2, y2 = geometry.view_to_portrait(u, v, rot)
                self.assertAlmostEqual(x, x2)
                self.assertAlmostEqual(y, y2)

    def test_mapping_matches_pixel_rotation(self):
        # A marked pixel must land where view_to_portrait says it came from.
        h, w = 40, 20
        frame = np.zeros((h, w, 1), dtype=np.uint8)
        py, px = 5, 3
        frame[py, px] = 255
        for rot in (0, 90, 180, 270):
            out = geometry.rotate_frame(frame, rot)
            vh, vw = out.shape[:2]
            vy, vx = np.argwhere(out[..., 0] == 255)[0]
            x, y = geometry.view_to_portrait((vx + 0.5) / vw, (vy + 0.5) / vh, rot)
            self.assertAlmostEqual(x, (px + 0.5) / w, places=6)
            self.assertAlmostEqual(y, (py + 0.5) / h, places=6)

    def test_effective_rotation(self):
        self.assertEqual(geometry.effective_rotation(1), 0)
        self.assertEqual(geometry.effective_rotation(6, 90), 180)
        self.assertEqual(geometry.effective_rotation(99, 270), 270)


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.m = mapping.InputMapper(mapping.default_profile(),
                                     lambda *e: self.events.append(e), lambda: (1000, 2000))

    def test_button_down_up(self):
        self.m.key_down("space")
        self.m.key_up("space")
        self.assertEqual([e[0] for e in self.events], [P.TOUCH_DOWN, P.TOUCH_UP])
        self.assertEqual(self.events[0][2:], (0.88, 0.62))

    def test_key_repeat_ignored(self):
        self.m.key_down("space")
        self.m.key_down("space")
        self.assertEqual(len(self.events), 1)

    def test_stick_lifecycle(self):
        self.m.key_down("w")
        self.assertEqual([e[0] for e in self.events], [P.TOUCH_DOWN, P.TOUCH_MOVE])
        finger, x, y = self.events[-1][1:]
        self.assertAlmostEqual(x, 0.18)
        self.assertLess(y, 0.72)                       # moved up
        self.m.key_down("d")                           # diagonal stays inside the radius
        _, dx, dy = self.events[-1][1:]
        self.assertGreater(dx, 0.18)
        r = 0.10
        self.assertAlmostEqual(((dx - 0.18) ** 2 + ((dy - 0.72) / 0.5) ** 2) ** 0.5, r, places=6)
        self.m.key_up("w")
        self.m.key_up("d")
        self.assertEqual(self.events[-1][0], P.TOUCH_UP)
        downs = [e for e in self.events if e[0] == P.TOUCH_DOWN]
        self.assertEqual(len(downs), 1)                # one finger down, not re-pressed

    def test_opposite_keys_cancel(self):
        self.m.key_down("a")
        self.m.key_down("d")
        self.assertEqual(self.events[-1][0], P.TOUCH_UP)

    def test_mouse_and_release_all(self):
        self.m.mouse_move(0.5, 0.5)                    # ignored, button not down
        self.assertEqual(self.events, [])
        self.m.mouse_down(0.5, 0.5)
        self.m.mouse_move(0.6, 0.6)
        self.m.key_down("space")
        self.m.key_down("w")
        self.events.clear()
        self.m.release_all()
        ups = sorted(e[1] for e in self.events if e[0] == P.TOUCH_UP)
        self.assertEqual(len(ups), 3)
        self.assertIn(mapping.MOUSE_FINGER, ups)
        self.events.clear()
        self.m.key_up("space")                         # nothing held anymore
        self.assertEqual(self.events, [])

    def test_validate_rejects_bad_profiles(self):
        bad = [
            {},
            {"bindings": [{"type": "button", "key": "a", "x": 2, "y": 0}]},
            {"bindings": [{"type": "button", "key": "", "x": 0, "y": 0}]},
            {"bindings": [{"type": "stick", "x": 0, "y": 0, "radius": 0.1, "keys": {"up": "w"}}]},
            {"bindings": [{"type": "wat", "x": 0, "y": 0}]},
            {"bindings": [], "rotation_offset": 45},
        ]
        for p in bad:
            with self.assertRaises(ValueError, msg=str(p)):
                mapping.validate_profile(p)

    def test_profile_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["PHONEMIRROR_HOME"] = d
            try:
                p = mapping.default_profile()
                p["name"] = "my game"
                mapping.save_profile(p)
                self.assertEqual(mapping.load_profile("my game"), p)
                self.assertEqual(mapping.load_profile("missing")["name"], "missing")
                with self.assertRaises(ValueError):
                    mapping.load_profile("../evil")
            finally:
                del os.environ["PHONEMIRROR_HOME"]


if __name__ == "__main__":
    unittest.main()
