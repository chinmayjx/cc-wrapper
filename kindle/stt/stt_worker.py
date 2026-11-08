"""Whisper speech to text (faster-whisper), for stt_server.py.

As a process of its own it reads requests on stdin: a u32 big-endian length, then that many bytes of 16 kHz mono
16-bit PCM, and answers each with a JSON line {"text": ...}. Prints {"ready": true} once the model is loaded.
"""

import json
import os
import struct
import sys

import numpy as np
from faster_whisper import WhisperModel

# Words Whisper wouldn't guess on its own ("Hey Claude" came out as "Hey Lord").
PROMPT = "Talking to Claude, a coding assistant, about the devbox, the Kindle and code."


def load(name, device="cpu"):
    if device == "cuda":
        return WhisperModel(name, device="cuda", compute_type="int8_float16")
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)


def transcribe(model, pcm, beam=1):
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    segments, _ = model.transcribe(audio, language="en", beam_size=beam, vad_filter=True,
                                   condition_on_previous_text=False, initial_prompt=PROMPT)
    return " ".join(s.text.strip() for s in segments).strip()


def main():
    model = load(os.environ.get("INK_STT_MODEL", "small.en"))
    print(json.dumps({"ready": True}), flush=True)
    inp = sys.stdin.buffer
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return
        pcm = inp.read(struct.unpack(">I", head)[0])
        print(json.dumps({"text": transcribe(model, pcm)}), flush=True)


if __name__ == "__main__":
    main()
