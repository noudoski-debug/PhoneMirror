import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phonemirror import protocol as P
from phonemirror.session import Session


class FakeDecoder:
    def decode(self, data):
        if data == b"bad":
            raise ValueError("corrupt")
        return [data]


class SessionTests(unittest.TestCase):
    def test_streaming_and_touch(self):
        device, pc = socket.socketpair()
        frames, statuses, made = [], [], []
        got_frame = threading.Event()

        def factory():
            made.append(1)
            return FakeDecoder()

        def on_frame(f, o):
            frames.append((f, o))
            got_frame.set()

        s = Session(lambda: pc, factory, on_frame, statuses.append, retry_delay=0.05)
        s.start()
        try:
            # session asks for a keyframe right after connecting
            self.assertEqual(P.read_message(device), (P.MSG_KEYFRAME_REQ, b""))
            device.sendall(P.pack_hello() + P.pack_video(6, True, b"frame1"))
            self.assertTrue(got_frame.wait(2))
            self.assertEqual(frames[0], (b"frame1", 6))
            self.assertIn("streaming", statuses)

            self.assertTrue(s.send_touch(P.TOUCH_DOWN, 2, 0.25, 0.75))
            t, payload = P.read_message(device)
            self.assertEqual(t, P.MSG_TOUCH)
            self.assertEqual(P.unpack_touch(payload), (P.TOUCH_DOWN, 2, 0.25, 0.75))

            # corrupt frame: decoder is rebuilt and a keyframe is requested
            device.sendall(P.pack_video(1, False, b"bad"))
            self.assertEqual(P.read_message(device)[0], P.MSG_KEYFRAME_REQ)
            self.assertEqual(len(made), 2)

            # unknown message types are ignored
            device.sendall(P.pack_message(0x7F, b"zzz") + P.pack_video(1, False, b"frame2"))
            deadline = time.time() + 2
            while len(frames) < 2 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(frames[1][0], b"frame2")
        finally:
            s.stop()
            device.close()

    def test_version_mismatch_disconnects_and_retries(self):
        statuses = []
        attempts = []

        def connect():
            attempts.append(1)
            a, b = socket.socketpair()
            a.sendall(P.pack_hello(version=99))
            return b

        s = Session(connect, FakeDecoder, lambda f, o: None, statuses.append, retry_delay=0.05)
        s.start()
        deadline = time.time() + 2
        while len(attempts) < 2 and time.time() < deadline:
            time.sleep(0.01)
        s.stop()
        self.assertGreaterEqual(len(attempts), 2)
        self.assertTrue(any("mismatch" in x for x in statuses))

    def test_connect_failure_retries(self):
        statuses = []
        calls = []

        def connect():
            calls.append(1)
            raise RuntimeError("no device")

        s = Session(connect, FakeDecoder, lambda f, o: None, statuses.append, retry_delay=0.02)
        s.start()
        time.sleep(0.15)
        s.stop()
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(s.send_touch(P.TOUCH_UP, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
