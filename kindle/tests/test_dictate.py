import json
import math
import tempfile
import time
import unittest
from array import array
from unittest import mock

from ink import dictate

S = dictate.BYTES_PER_S


def tone(seconds, level=3000):
    n = int(seconds * 16000)
    return array("h", (int(level * math.sin(i / 5)) for i in range(n))).tobytes()


def quiet(seconds):
    return tone(seconds, level=20)


class PauseTest(unittest.TestCase):
    def test_cuts_in_the_last_pause_after_min(self):
        pcm = tone(2) + quiet(1) + tone(3) + quiet(1) + tone(1)
        at = dictate.pause_at(pcm, min_s=4)
        self.assertTrue(6 * S <= at <= 7 * S, at / S)  # the second pause; the first is before 4 s

    def test_no_pause_or_too_short(self):
        self.assertIsNone(dictate.pause_at(tone(10)))
        self.assertIsNone(dictate.pause_at(tone(1) + quiet(1), min_s=4))


class ScribeTest(unittest.TestCase):
    def setUp(self):  # Scribe, whatever this machine's switch says
        patcher = mock.patch.object(dictate, "ENGINE_FILE", dictate.Path("/nonexistent/stt"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sends_a_wav_with_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            key = dictate.Path(d) / "api.env"
            key.write_text("ELEVENLABS_API_KEY=sk_test\n")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"text": " hello there "}'
            with mock.patch.object(dictate, "KEY_FILE", key), \
                    mock.patch.object(dictate.urllib.request, "urlopen", return_value=response) as opened:
                self.assertEqual(dictate.transcribe(tone(1)), "hello there")
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, dictate.SCRIBE_URL)
        self.assertEqual(req.get_header("Xi-api-key"), "sk_test")
        self.assertIn(b'name="model_id"\r\n\r\nscribe_v2', req.data)
        self.assertIn(b'name="language_code"\r\n\r\nen', req.data)
        self.assertIn(b"RIFF", req.data)

    def test_quiet_audio_is_raised(self):
        quiet_speech = tone(1, level=300)
        raised = array("h", dictate.louder(quiet_speech))
        self.assertGreater(max(raised), 8000)
        self.assertEqual(dictate.louder(tone(1, level=20000)), tone(1, level=20000))  # loud enough already

    def test_no_key(self):
        with mock.patch.object(dictate, "KEY_FILE", dictate.Path("/nonexistent/api.env")):
            with self.assertRaises(dictate.Failed):
                dictate.transcribe(tone(1))


class LaptopTest(unittest.TestCase):
    def test_the_switch_sends_raw_pcm_to_the_laptop(self):
        with tempfile.TemporaryDirectory() as d:
            switch = dictate.Path(d) / "stt"
            switch.write_text("laptop\n")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"text": " hi "}'
            with mock.patch.object(dictate, "ENGINE_FILE", switch), \
                    mock.patch.object(dictate.urllib.request, "urlopen", return_value=response) as opened:
                self.assertEqual(dictate.transcribe(tone(1, level=20000)), "hi")
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, dictate.LAPTOP + "/transcribe")
        self.assertEqual(req.data, tone(1, level=20000))

    def test_laptop_off(self):
        with tempfile.TemporaryDirectory() as d:
            switch = dictate.Path(d) / "stt"
            switch.write_text("laptop")
            with mock.patch.object(dictate, "ENGINE_FILE", switch), \
                    mock.patch.object(dictate.urllib.request, "urlopen", side_effect=OSError("timed out")):
                with self.assertRaises(dictate.Failed):
                    dictate.transcribe(tone(1))


class EngineTest(unittest.TestCase):
    def test_config_names_the_engine_and_the_file_wins(self):
        with mock.patch.object(dictate, "ENGINE_FILE", None), mock.patch.object(dictate.config, "STT_ENGINE", "deepgram"):
            self.assertIs(dictate.engine(), dictate.deepgram)
        with tempfile.TemporaryDirectory() as d:
            switch = dictate.Path(d) / "stt"
            switch.write_text("whisper\n")
            with mock.patch.object(dictate, "ENGINE_FILE", switch), mock.patch.object(dictate.config, "STT_ENGINE", "deepgram"):
                self.assertIs(dictate.engine(), dictate.laptop)

    def test_key_from_the_environment(self):
        with mock.patch.dict(dictate.os.environ, {"DEEPGRAM_API_KEY": "dg_env"}), \
                mock.patch.object(dictate, "DEEPGRAM_KEY", dictate.Path("/nonexistent/api.env")):
            self.assertEqual(dictate.deepgram.key(), "dg_env")

    def test_no_whisper_server(self):
        with mock.patch.object(dictate, "LAPTOP", ""):
            with self.assertRaisesRegex(dictate.Failed, "KINDLE_WHISPER_URL"):
                dictate.laptop.text(tone(1))


class DeepgramTest(unittest.TestCase):
    def test_sends_a_wav_with_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            switch, key = dictate.Path(d) / "stt", dictate.Path(d) / "api.env"
            switch.write_text("deepgram\n")
            key.write_text("DEEPGRAM_API_KEY=dg_test\n")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({"results": {"channels": [
                {"alternatives": [{"transcript": " Hello there. "}]}]}}).encode()
            with mock.patch.object(dictate, "ENGINE_FILE", switch), mock.patch.object(dictate, "DEEPGRAM_KEY", key), \
                    mock.patch.object(dictate.urllib.request, "urlopen", return_value=response) as opened:
                self.assertEqual(dictate.transcribe(tone(1)), "Hello there.")
        req = opened.call_args.args[0]
        self.assertTrue(req.full_url.startswith("https://api.deepgram.com/v1/listen?model=nova-3"))
        self.assertEqual(req.get_header("Authorization"), "Token dg_test")
        self.assertTrue(req.data.startswith(b"RIFF"))

    def test_no_key(self):
        with tempfile.TemporaryDirectory() as d:
            switch = dictate.Path(d) / "stt"
            switch.write_text("deepgram")
            with mock.patch.object(dictate, "ENGINE_FILE", switch), \
                    mock.patch.object(dictate, "DEEPGRAM_KEY", dictate.Path("/nonexistent/api.env")):
                with self.assertRaises(dictate.Failed):
                    dictate.transcribe(tone(1))


class MissedTest(unittest.TestCase):
    def test_speech_vs_room_noise(self):
        self.assertTrue(dictate.has_speech(tone(3, level=3000)))
        self.assertFalse(dictate.has_speech(tone(3, level=150)))
        self.assertFalse(dictate.has_speech(tone(1, level=3000)))  # under 2 s

    def test_an_empty_piece_with_speech_goes_to_the_laptop(self):
        with mock.patch.object(dictate, "engine", return_value=dictate.deepgram), \
                mock.patch.object(dictate.laptop, "text", return_value="rescued") as laptop:
            self.assertEqual(dictate._second_try(tone(3, level=3000)), "rescued")
        laptop.assert_called_once()


class DictationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.keep = mock.patch.object(dictate, "LAST_AUDIO", dictate.Path(self.dir.name) / "last.wav")
        self.keep.start()

    def tearDown(self):
        self.keep.stop()
        self.dir.cleanup()

    def test_pieces_while_talking_and_the_rest_on_stop(self):
        audio = [tone(6) + quiet(1), tone(2) + quiet(1)]  # the first pause after 8 s is where it cuts
        calls = []

        def mic(action):
            calls.append(action)
            if action == "read":
                return audio.pop(0) if audio else b""
            return tone(1) if action == "stop" else b""

        texts = iter(["first part", "the rest"])
        with mock.patch.object(dictate, "_mic", side_effect=mic), \
                mock.patch.object(dictate, "transcribe", side_effect=lambda pcm: next(texts)), \
                mock.patch.object(dictate._Dictation, "run", dictate._Dictation.run):
            dictate._current = None
            self.assertEqual(dictate.start()["state"], "recording")
            for _ in range(50):
                if dictate.state()["text"]:
                    break
                time.sleep(0.1)
            self.assertEqual(dictate.state()["text"], "first part")  # cut at the pause, before stopping
            dictate.stop()
            for _ in range(50):
                if dictate.state()["state"] == "done":
                    break
                time.sleep(0.1)
        self.assertEqual(dictate.state(), {"state": "done", "text": "first part the rest", "seconds": 11, "error": "",
                                           "ended": "", "missed": 0})
        self.assertEqual(calls[0], "start")
        self.assertEqual(calls[-1], "stop")

    def test_stops_when_the_kindle_goes_away(self):
        calls = []

        def mic(action):
            calls.append(action)
            return quiet(1) if action == "read" else b""

        with mock.patch.object(dictate, "_mic", side_effect=mic), \
                mock.patch.object(dictate, "transcribe", return_value=""), \
                mock.patch.object(dictate, "KINDLE_GONE_S", 2):
            dictate._current = None
            dictate.start()  # and then nobody asks how it's going
            for _ in range(80):
                if dictate._current.state == "done":
                    break
                time.sleep(0.1)
        self.assertEqual(dictate._current.state, "done")
        self.assertEqual(dictate._current.ended, "the Kindle stopped checking in")
        self.assertEqual(calls[-1], "stop")

    def test_phone_not_ready(self):
        with mock.patch.object(dictate, "_mic", side_effect=dictate.Failed("dictation is off")):
            dictate._current = None
            with self.assertRaises(dictate.Failed):
                dictate.start()
        self.assertEqual(dictate.state()["state"], "idle")


class MicTest(unittest.TestCase):
    """_mic goes to the server's end of the phone's WebSocket (ink/mic.py)."""

    def test_actions_go_to_the_phone(self):
        phone = mock.Mock()
        phone.read.return_value = b"pcm"
        phone.stop.return_value = b"rest"
        with mock.patch.object(dictate, "phone", phone):
            self.assertEqual(dictate._mic("start"), phone.start.return_value)
            self.assertEqual(dictate._mic("read"), b"pcm")
            self.assertEqual(dictate._mic("stop"), b"rest")
        phone.start.assert_called_once_with()

    def test_no_phone_is_a_failure_the_kindle_shows(self):
        with mock.patch.object(dictate, "phone", dictate.mic.PhoneMic()):
            with self.assertRaises(dictate.Failed) as e:
                dictate._mic("start")
        self.assertEqual(str(e.exception), "the phone's mic isn't on: in the Claude Code app, pull down the tray and turn on "
                                           "Kindle dictation")

    def test_dictation_with_no_phone(self):
        with mock.patch.object(dictate, "phone", dictate.mic.PhoneMic()):
            dictate._current = None
            with self.assertRaises(dictate.Failed):
                dictate.start()
        self.assertEqual(dictate.state()["state"], "idle")


if __name__ == "__main__":
    unittest.main()
