"""/api/mic end to end: the mic's real listener, a fake phone speaking the WebSocket protocol (ink/mic.py)."""

import base64
import os
import socket
import struct
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from ink import dictate, mic, websocket


class FakePhone:
    """A WebSocket client the way the Claude Code app's MicService is one: masked frames, "ready <name>" first."""

    def __init__(self, port, name="Test Phone"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /api/mic HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(1)
        assert head.startswith(b"HTTP/1.1 101"), head
        assert websocket.accept_key(key).encode() in head, head
        self.text(f"ready {name}")

    def send(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = struct.pack("!BB", 0x80 | opcode, 0x80 | n) if n < 126 else struct.pack("!BBH", 0x80 | opcode, 0xFE, n)
        self.sock.sendall(head + mask + websocket.unmask(payload, mask))

    def text(self, t):
        self.send(websocket.TEXT, t.encode())

    def recv_text(self):
        b0, b1 = self.sock.recv(2, socket.MSG_WAITALL)
        data = self.sock.recv(b1 & 0x7F, socket.MSG_WAITALL)
        assert b0 & 0x0F == websocket.TEXT, b0
        return data.decode()

    def close(self):
        self.sock.close()


def wait(cond, s=3):
    end = time.time() + s
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class MicSocketTest(unittest.TestCase):
    def setUp(self):
        self.phone_mic = mic.PhoneMic()
        patcher = mock.patch.object(mic, "phone", self.phone_mic)
        patcher.start()
        self.addCleanup(patcher.stop)
        mic.MicHandler.log_message = lambda *a: None
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), mic.MicHandler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def test_record_through_the_phone(self):
        phone = FakePhone(self.port)
        self.addCleanup(phone.close)
        self.assertTrue(wait(lambda: self.phone_mic.connected() == "Test Phone"))
        with mock.patch.object(dictate, "phone", self.phone_mic):
            dictate._mic("start")
            self.assertEqual(phone.recv_text(), "start")
            phone.send(websocket.BINARY, b"\x01\x00" * 1600)  # 100 ms
            phone.send(websocket.BINARY, b"\x02\x00" * 200)
            self.assertTrue(wait(lambda: len(self.phone_mic._buf) == 3600))
            self.assertEqual(dictate._mic("read"), b"\x01\x00" * 1600 + b"\x02\x00" * 200)
            self.assertEqual(dictate._mic("read"), b"")

            def phone_side():  # the last frame arrives after "stop", then "stopped"
                self.assertEqual(phone.recv_text(), "stop")
                phone.send(websocket.BINARY, b"\x03\x00" * 100)
                phone.text("stopped")
            t = threading.Thread(target=phone_side)
            t.start()
            self.assertEqual(dictate._mic("stop"), b"\x03\x00" * 100)
            t.join()

    def test_audio_outside_a_recording_is_dropped(self):
        phone = FakePhone(self.port)
        self.addCleanup(phone.close)
        self.assertTrue(wait(lambda: self.phone_mic.connected()))
        phone.send(websocket.BINARY, b"\x01\x00" * 100)
        time.sleep(0.2)
        self.assertEqual(self.phone_mic.read(), b"")

    def test_phone_error(self):
        phone = FakePhone(self.port)
        self.addCleanup(phone.close)
        self.assertTrue(wait(lambda: self.phone_mic.connected()))
        self.phone_mic.start()
        self.assertEqual(phone.recv_text(), "start")
        phone.text("error the microphone is busy")
        self.assertTrue(wait(lambda: self.phone_mic._error))
        with mock.patch.object(dictate, "phone", self.phone_mic):
            with self.assertRaisesRegex(dictate.Failed, "the microphone is busy"):
                dictate._mic("read")

    def test_disconnect_mid_recording_and_none_left(self):
        phone = FakePhone(self.port)
        self.assertTrue(wait(lambda: self.phone_mic.connected()))
        self.phone_mic.start()
        phone.close()
        self.assertTrue(wait(lambda: self.phone_mic.connected() is None))
        with mock.patch.object(dictate, "phone", self.phone_mic):
            with self.assertRaisesRegex(dictate.Failed, "disconnected while recording"):
                dictate._mic("read")
            with self.assertRaisesRegex(dictate.Failed, "pull down the tray"):
                dictate._mic("start")

    def test_a_new_connection_replaces_the_old(self):
        first = FakePhone(self.port, "First")
        self.addCleanup(first.close)
        self.assertTrue(wait(lambda: self.phone_mic.connected() == "First"))
        second = FakePhone(self.port, "Second")
        self.addCleanup(second.close)
        self.assertTrue(wait(lambda: self.phone_mic.connected() == "Second"))
        self.assertEqual(first.sock.recv(1), b"")  # the server closed the old one
        self.phone_mic.start()
        self.assertEqual(second.recv_text(), "start")

    def test_plain_get_is_refused(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /api/mic HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertTrue(s.recv(100).startswith(b"HTTP/1.0 400"))
        s.close()

    def test_only_the_mic_path(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertTrue(s.recv(100).startswith(b"HTTP/1.0 404"))
        s.close()


if __name__ == "__main__":
    unittest.main()
