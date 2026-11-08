"""Dictation for the Kindle panel: the phone records, a speech-to-text service writes it down.

The Kindle has no microphone, so its Mic button records through the phone: the Claude Code app (CC Wrapper)
keeps a WebSocket open to /api/mic while "Kindle dictation" is on in its settings tray (ink/mic.py).
start() asks the phone to record; a thread pulls the audio every second and transcribes it in pieces cut at
pauses, so stopping leaves only the last few seconds to do. stop() ends the recording; state() is what the
Kindle polls (every second, only while it's dictating).

Pieces go to the engine config names (KINDLE_STT_ENGINE, or the file KINDLE_STT_ENGINE_FILE, read per piece so
switching needs no restart): "deepgram" (Nova-3, DEEPGRAM_API_KEY in KINDLE_DEEPGRAM_KEY_FILE), "whisper"
(stt/stt_server.py on a machine with a GPU at KINDLE_WHISPER_URL; free but weaker, and only while that machine
is on), else ElevenLabs Scribe (ELEVENLABS_API_KEY in KINDLE_ELEVENLABS_KEY_FILE). A key may also come from the
environment.
"""

import io
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from array import array
from pathlib import Path

from ink import config, mic
from ink.mic import NotReady, phone

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SCRIBE_MODEL = "scribe_v2"
KEY_FILE = config.ELEVENLABS_KEY_FILE
LAPTOP = config.WHISPER_URL
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen?model=nova-3&language=en&smart_format=true"
DEEPGRAM_KEY = config.DEEPGRAM_KEY_FILE
ENGINE_FILE = config.STT_ENGINE_FILE
BYTES_PER_S = 16000 * 2  # 16 kHz mono, 16-bit
FRAME = BYTES_PER_S // 10  # 100 ms
MIN_PIECE_S, MAX_PIECE_S, PAUSE_FRAMES = 8, 30, 6  # cut at a 0.6 s pause once a piece is 8 s; always by 30 s
# A forgotten recording stops itself: when the Kindle stops asking how it's going (page closed, Kindle asleep),
# or at the longest a dictation may run. What was said so far is still written down.
KINDLE_GONE_S = 15
MAX_RECORDING_S = 1800
# The last dictation's audio, overwritten by the next one: for checking what the phone actually heard.
LAST_AUDIO = config.CACHE_DIR / "dictation-last.wav"
# A piece with speech in it that came back empty: kept (the last few) to find out why.
MISSED_DIR = config.CACHE_DIR / "dictation-missed"
KEEP_MISSED = 10
SPEECH_LEVEL, SPEECH_S = 600, 2.0  # at least 2 s of frames this loud is speech, not room noise


def _log(msg):
    sys.stderr.write(f"dictate: {msg}\n")


class Failed(Exception):
    pass


def _mic(action):
    """The phone's mic (ink/mic.py; the Claude Code app connects to /api/mic): "start" starts a fresh recording, "read"
    returns the PCM since the last read, "stop" stops and returns the rest."""
    try:
        return getattr(phone, action)()
    except NotReady as e:
        raise Failed(str(e)) from None


def _key(name, path):
    """An API key: from the environment, else a NAME=value line in its key file (never in the repo)."""
    if os.environ.get(name):
        return os.environ[name].strip()
    try:
        for line in path.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return None


# ---- Engines ---------------------------------------------------------------------------------------------

class _Scribe:
    """ElevenLabs speech-to-text."""

    def key(self):
        key = _key("ELEVENLABS_API_KEY", KEY_FILE)
        if not key:
            raise Failed(f"no ElevenLabs key ({KEY_FILE})")
        return key

    def text(self, pcm):
        boundary = uuid.uuid4().hex
        parts = [(b'name="model_id"', SCRIBE_MODEL.encode()), (b'name="tag_audio_events"', b"false"),
                 (b'name="language_code"', b"en"),  # else a mumbled piece can come back in Chinese or Hindi
                 (b'name="file"; filename="piece.wav"\r\nContent-Type: audio/wav', _wav(pcm))]
        body = b"".join(b"--" + boundary.encode() + b"\r\nContent-Disposition: form-data; " + head + b"\r\n\r\n"
                        + value + b"\r\n" for head, value in parts) + b"--" + boundary.encode() + b"--\r\n"
        req = urllib.request.Request(SCRIBE_URL, data=body, method="POST", headers={
            "xi-api-key": self.key(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return (json.loads(r.read()).get("text") or "").strip()
        except urllib.error.HTTPError as e:
            raise Failed(f"ElevenLabs said {e.code}: {e.read()[:200].decode(errors='replace')}") from None
        except OSError as e:
            raise Failed(f"ElevenLabs unreachable: {e}") from None


scribe = _Scribe()


class _Laptop:
    """Whisper (stt/stt_server.py) on another machine's GPU, at KINDLE_WHISPER_URL."""

    def text(self, pcm):
        if not LAPTOP:
            raise Failed("no Whisper server (KINDLE_WHISPER_URL)")
        req = urllib.request.Request(LAPTOP + "/transcribe", data=pcm, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return (json.loads(r.read()).get("text") or "").strip()
        except urllib.error.HTTPError as e:
            raise Failed(f"the Whisper server said {e.code}") from None
        except OSError as e:
            raise Failed(f"the Whisper server is unreachable (is that machine on?): {e}") from None


laptop = _Laptop()


def _wav(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return out.getvalue()


class _Deepgram:
    """Deepgram's Nova-3."""

    def key(self):
        key = _key("DEEPGRAM_API_KEY", DEEPGRAM_KEY)
        if not key:
            raise Failed(f"no Deepgram key ({DEEPGRAM_KEY})")
        return key

    def text(self, pcm):
        return self.audio(_wav(pcm), "audio/wav")

    def audio(self, data, ctype):
        """Any recording Deepgram reads (WAV, the browser's WebM/Ogg/MP4) to text."""
        req = urllib.request.Request(DEEPGRAM_URL, data=data, method="POST", headers={
            "Authorization": f"Token {self.key()}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                alts = json.loads(r.read())["results"]["channels"][0]["alternatives"]
                return (alts[0].get("transcript") or "").strip() if alts else ""
        except urllib.error.HTTPError as e:
            raise Failed(f"Deepgram said {e.code}: {e.read()[:200].decode(errors='replace')}") from None
        except OSError as e:
            raise Failed(f"Deepgram unreachable: {e}") from None


deepgram = _Deepgram()
ENGINES = {"whisper": laptop, "laptop": laptop, "deepgram": deepgram, "elevenlabs": scribe}


def engine():
    name = config.STT_ENGINE
    if ENGINE_FILE:
        try:
            name = ENGINE_FILE.read_text().strip() or name
        except OSError:
            pass
    return ENGINES.get(name, scribe)


def louder(pcm, target=12000, most=30.0):
    """The piece raised so its loud parts reach `target`: the phone's speech-recognition mic has no automatic
    gain, and a phone set down across the room comes through ~40 dB down, quiet enough to read as silence."""
    a = array("h", pcm)
    if not a:
        return pcm
    ranked = sorted(abs(x) for x in a[::16])
    loud = ranked[int(len(ranked) * 0.995)] or 1  # near the peak, ignoring the odd click
    gain = min(most, target / loud)
    if gain <= 1.2:
        return pcm
    return array("h", (max(-32768, min(32767, int(x * gain))) for x in a)).tobytes()


def transcribe(pcm):
    t = time.time()
    levels = _levels(pcm)
    text = engine().text(louder(pcm))
    _log(f"{len(pcm) / BYTES_PER_S:.1f} s of audio in {time.time() - t:.1f} s, "
         f"loudness median {sorted(levels)[len(levels) // 2] if levels else 0:.0f} peak {max(levels, default=0):.0f}, "
         f"{len(text.split())} words")
    return text


def has_speech(pcm):
    return sum(1 for x in _levels(pcm) if x >= SPEECH_LEVEL) * 0.1 >= SPEECH_S


def _second_try(pcm):
    """A piece with speech that came back empty: the other engine (Deepgram sometimes returns nothing for
    a whole piece, with no error). Whisper for the APIs (if there is one), else Deepgram; '' if it fails too."""
    other = laptop if engine() is not laptop and LAPTOP else deepgram
    if other is engine():
        return ""
    try:
        text = other.text(louder(pcm))
    except Failed as e:
        _log(f"second try failed: {e}")
        return ""
    _log(f"empty piece; second try gave {len(text.split())} words")
    return text


def _keep_missed(pcm):
    try:
        MISSED_DIR.mkdir(parents=True, exist_ok=True)
        with wave.open(str(MISSED_DIR / time.strftime("%Y%m%d-%H%M%S.wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        for old in sorted(MISSED_DIR.glob("*.wav"))[:-KEEP_MISSED]:
            old.unlink()
    except OSError as e:
        _log(f"couldn't keep a missed piece: {e}")


# ---- Cutting audio at pauses -----------------------------------------------------------------------------

def _levels(pcm):
    """Loudness (RMS) of each 100 ms frame."""
    out = []
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        a = array("h", pcm[i:i + FRAME])
        out.append(math.sqrt(sum(x * x for x in a) / len(a)))
    return out


def pause_at(pcm, min_s=MIN_PIECE_S):
    """Byte offset in the middle of the last pause after min_s, or None. A pause is PAUSE_FRAMES quiet frames;
    quiet is relative to this recording's own background level, since phones and rooms differ."""
    levels = _levels(pcm)
    if len(levels) < min_s * 10 + PAUSE_FRAMES:
        return None
    ranked = sorted(levels)
    # Near the quietest fifth, and well under the typical level (else steady talk would count as quiet).
    quiet = max(200.0, min(ranked[len(ranked) // 5] * 2.5, ranked[len(ranked) // 2] / 3))
    run = 0
    for i in range(len(levels) - 1, min_s * 10 - 1, -1):
        run = run + 1 if levels[i] < quiet else 0
        if run >= PAUSE_FRAMES:
            return (i + PAUSE_FRAMES // 2) * FRAME
    return None


# ---- A dictation -----------------------------------------------------------------------------------------

class _Dictation(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.state = "recording"  # then transcribing, then done or error
        self.pieces, self.pending, self.seconds, self.error = [], bytearray(), 0.0, ""
        self.stopping = threading.Event()
        self.audio = bytearray()  # everything recorded, for LAST_AUDIO
        self.seen = time.time()  # the Kindle's last look (state())
        self.ended = ""  # why recording stopped by itself
        self.missed = 0.0  # seconds of speech no engine made out

    def run(self):
        try:
            while not self.stopping.wait(1):
                self._add(_mic("read"))
                self._cut(final=False)
                if time.time() - self.seen > KINDLE_GONE_S:
                    self.ended = "the Kindle stopped checking in"
                    break
                if self.seconds >= MAX_RECORDING_S:
                    self.ended = f"{MAX_RECORDING_S // 60} minute limit"
                    break
            self.state = "transcribing"
            self._add(_mic("stop"))
            stopped = time.time()
            self._cut(final=True)
            self.state = "done"
            _log(f"done: {self.seconds:.0f} s recorded, {time.time() - stopped:.1f} s from stop to text"
                 + (f", stopped by itself: {self.ended}" if self.ended else ""))
            self._keep()
        except Failed as e:
            self.error, self.state = str(e), "error"
            _log(f"failed: {e}")
            try:
                _mic("stop")
            except Failed:
                pass

    def _keep(self):
        try:
            LAST_AUDIO.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(LAST_AUDIO), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(self.audio))
        except OSError as e:
            _log(f"couldn't keep the audio: {e}")

    def _add(self, pcm):
        self.pending += pcm
        self.audio += pcm
        self.seconds += len(pcm) / BYTES_PER_S

    def _cut(self, final):
        if final:
            at = len(self.pending)
        elif len(self.pending) >= MAX_PIECE_S * BYTES_PER_S:
            at = pause_at(bytes(self.pending)) or len(self.pending)
        else:
            at = pause_at(bytes(self.pending))
        if not at:
            return
        at -= at % 2
        piece = bytes(self.pending[:at])
        del self.pending[:at]
        text = transcribe(piece)
        if not text and has_speech(piece):
            text = _second_try(piece)
        if text:
            self.pieces.append(text)
        elif has_speech(piece):
            self.missed += len(piece) / BYTES_PER_S
            _keep_missed(piece)

    def view(self):
        return {"state": self.state, "text": " ".join(self.pieces), "seconds": round(self.seconds),
                "error": self.error, "ended": self.ended, "missed": round(self.missed)}


_lock = threading.Lock()
_current = None


def start():
    global _current
    with _lock:
        if _current and _current.state in ("recording", "transcribing"):
            return _current.view()
        _mic("start")
        _current = _Dictation()
        _current.start()
        return _current.view()


def stop():
    with _lock:
        if _current:
            _current.stopping.set()
            return _current.view()
    return state()


def state():
    if not _current:
        return {"state": "idle", "text": "", "seconds": 0, "error": "", "ended": ""}
    _current.seen = time.time()
    return _current.view()
