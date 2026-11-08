"""The server side of a WebSocket (RFC 6455), just what the phone's mic (ink/mic.py) needs: the handshake's
accept key and frames.
"""

import base64
import errno
import hashlib
import select
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1_000_000

TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA


def accept_key(key):
    """Sec-WebSocket-Accept for a client's Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def is_upgrade(headers):
    """Whether these request headers (case-insensitive mapping) ask for a WebSocket."""
    return (headers.get("upgrade", "").lower() == "websocket" and "upgrade" in headers.get("connection", "").lower()
            and headers.get("sec-websocket-version") == "13" and bool(headers.get("sec-websocket-key")))


def parse_frame(buf, max_frame=MAX_FRAME):
    """One client frame from the front of buf: (opcode, payload, fin, the rest of buf), or None when buf holds
    only part of it. Client frames are masked."""
    if len(buf) < 2:
        return None
    b0, b1 = buf[0], buf[1]
    if b0 & 0x70:
        raise ValueError("reserved bits")
    if not (b1 & 0x80):
        raise ValueError("unmasked frame")
    length = b1 & 0x7F
    i = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = struct.unpack("!H", buf[2:4])[0]
        i = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = struct.unpack("!Q", buf[2:10])[0]
        i = 10
    if length > max_frame:
        raise ValueError("frame too large")
    end = i + 4 + length
    if len(buf) < end:
        return None
    mask = buf[i:i + 4]
    payload = unmask(buf[i + 4:end], mask)
    return b0 & 0x0F, payload, bool(b0 & 0x80), buf[end:]


def unmask(data, mask):
    """XOR with the 4-byte mask (a whole-buffer integer XOR: audio frames are too big to do byte by byte)."""
    n = len(data)
    if not n:
        return b""
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


def frame(opcode, payload):
    """A server frame (unmasked, unfragmented)."""
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


def sendall(conn, data):
    """sendall that also works on a non-blocking socket."""
    while data:
        try:
            n = conn.send(data)
        except BlockingIOError:
            select.select([], [conn], [], 30)
            continue
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            raise
        if n == 0:
            raise ConnectionError
        data = data[n:]
