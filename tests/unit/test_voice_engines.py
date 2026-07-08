"""Engine dispatch tests for local STT (whisper) and Kokoro TTS — mocked backends."""
from __future__ import annotations

import io
import wave

import pytest

from aether.voice.stt import STT
from aether.voice.stt_local import LocalSTT
from aether.voice.tts import TTS
from aether.voice.tts_local import LocalTTS


class FakeLocalSTT:
    def available(self) -> bool:
        return True

    def transcribe(self, path) -> str:
        return f"transcribed:{path}"


def test_stt_local_engine_dispatch(monkeypatch):
    stt = STT(engine="local")
    monkeypatch.setattr(stt, "_local", FakeLocalSTT())
    assert stt.transcribe_file("x.wav") == "transcribed:x.wav"


def test_stt_local_unavailable_raises():
    stt = STT(engine="local")

    class Unavailable:
        def available(self):
            return False

    stt._local = Unavailable()
    with pytest.raises(RuntimeError, match="not configured"):
        stt.transcribe_file("x.wav")


def test_local_stt_no_backend_raises(monkeypatch):
    local = LocalSTT()
    monkeypatch.setattr(local, "_detect", lambda: None)
    assert local.available() is False
    with pytest.raises(RuntimeError, match="no local STT backend"):
        local.transcribe("x.wav")


class FakeLocalTTS:
    def __init__(self, ok: bool = True):
        self.ok = ok

    def available(self) -> bool:
        return self.ok

    def synthesize_bytes(self, text: str) -> bytes:
        return b"RIFFfake" + text.encode()


def test_tts_kokoro_synthesize_bytes(monkeypatch):
    tts = TTS(engine="kokoro")
    tts._local = FakeLocalTTS()
    data = tts.synthesize_bytes("hello")
    assert data.startswith(b"RIFF")
    assert b"hello" in data


def test_tts_kokoro_unavailable_raises():
    tts = TTS(engine="kokoro")
    tts._local = FakeLocalTTS(ok=False)
    with pytest.raises(RuntimeError, match="Kokoro not installed"):
        tts.synthesize_bytes("hello")


def test_tts_kokoro_speak_falls_back_to_say(monkeypatch):
    tts = TTS(engine="kokoro")
    tts._local = FakeLocalTTS(ok=False)
    spoken = []
    monkeypatch.setattr(tts, "_say", lambda text, blocking: spoken.append(text))
    tts.speak("fallback please")
    assert spoken == ["fallback please"]


def test_tts_kokoro_speak_plays_audio(monkeypatch):
    tts = TTS(engine="kokoro")
    tts._local = FakeLocalTTS()
    played = []
    monkeypatch.setattr(tts, "_play_bytes", lambda data, blocking, text: played.append(data))
    tts.speak("hi")
    assert played and played[0].startswith(b"RIFF")


def test_kokoro_voice_normalization():
    # non-Kokoro voice names (e.g. Orpheus 'troy') normalize to a Kokoro default
    tts = TTS(engine="kokoro", voice="troy")
    tts._local = None
    local = tts._local_tts()
    assert local.voice == "af_heart"
    tts2 = TTS(engine="kokoro", voice="am_adam")
    assert tts2._local_tts().voice == "am_adam"


def test_local_tts_wav_encoding(monkeypatch):
    """synthesize_bytes emits a valid 24kHz mono int16 WAV."""
    import numpy as np

    local = LocalTTS()

    class FakePipeline:
        def __call__(self, text, voice):
            yield "gs", "ps", np.zeros(2400, dtype=np.float32)

    monkeypatch.setattr(local, "_get_pipeline", lambda: FakePipeline())
    data = local.synthesize_bytes("hi")
    with wave.open(io.BytesIO(data)) as wf:
        assert wf.getframerate() == 24000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 2400
