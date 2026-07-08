"""Speech-to-text (push-to-talk).

Transcribes mic audio via Groq Whisper or OpenAI Whisper.
Falls back to typed input whenever the mic, deps, or API key are unavailable —
so Aether always runs, voice or not.
"""
from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path


class STT:
    def __init__(
        self,
        engine: str = "none",
        model: str = "whisper-large-v3-turbo",
        openai_api_key: str | None = None,
        groq_api_key: str | None = None,
        sample_rate: int = 16000,
        record_seconds: int = 8,
    ):
        self.engine = engine
        self.model = model
        self.openai_api_key = openai_api_key
        self.groq_api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        self.sample_rate = sample_rate
        self.record_seconds = record_seconds
        self._local = None  # lazy LocalSTT

    def _local_stt(self):
        if self._local is None:
            from .stt_local import LocalSTT
            self._local = LocalSTT(model=self.model if self.engine == "local" else "base")
        return self._local

    # --- public ---
    def listen(self, prompt: str = "You: ") -> str:
        if self.engine == "local" and self._can_record() and self._local_stt().available():
            try:
                path = self._record_wav()
                return self._postprocess(self._local_stt().transcribe(path))
            except Exception as e:  # noqa: BLE001
                print(f"[STT error, falling back to text: {e}]")
        if self.engine == "groq" and self._can_record() and self.groq_api_key:
            try:
                return self._listen_groq()
            except Exception as e:  # noqa: BLE001
                print(f"[STT error, falling back to text: {e}]")
        if self.engine == "openai" and self._can_record() and self.openai_api_key:
            try:
                return self._listen_openai()
            except Exception as e:  # noqa: BLE001
                print(f"[STT error, falling back to text: {e}]")
        return input(prompt).strip()

    def transcribe_file(self, path: str | Path) -> str:
        """Transcribe an on-disk WAV (or other supported) audio file."""
        if self.engine == "local" and self._local_stt().available():
            return self._local_stt().transcribe(path)
        if self.engine == "groq" and self.groq_api_key:
            return self._transcribe_groq(path)
        if self.engine == "openai" and self.openai_api_key:
            return self._transcribe_openai(path)
        raise RuntimeError(f"STT engine {self.engine!r} is not configured")

    # --- helpers ---
    def _can_record(self) -> bool:
        try:
            import sounddevice  # noqa: F401
            import numpy  # noqa: F401
            return True
        except Exception:
            return False

    def _record_wav(self) -> str:
        import sounddevice as sd

        print(f"🎙  Listening for {self.record_seconds}s — speak now…")
        frames = sd.rec(int(self.record_seconds * self.sample_rate),
                        samplerate=self.sample_rate, channels=1, dtype="int16")
        sd.wait()
        path = str(Path(tempfile.gettempdir()) / "aether-utterance.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(self.sample_rate)
            wf.writeframes(frames.tobytes())
        return path

    def _postprocess(self, text: str) -> str:
        text = (text or "").strip()
        try:
            from ..core.stop import is_stop_phrase
            if is_stop_phrase(text):
                return text
        except ImportError:
            pass
        print(f"🗣  Heard: {text}")
        return text

    def _listen_groq(self) -> str:
        path = self._record_wav()
        return self._postprocess(self._transcribe_groq(path))

    def _listen_openai(self) -> str:
        path = self._record_wav()
        return self._postprocess(self._transcribe_openai(path))

    def _transcribe_groq(self, path: str | Path) -> str:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        with open(path, "rb") as f:
            tr = client.audio.transcriptions.create(model=self.model, file=f)
        return (getattr(tr, "text", "") or "").strip()

    def _transcribe_openai(self, path: str | Path) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key)
        with open(path, "rb") as f:
            tr = client.audio.transcriptions.create(model=self.model, file=f)
        return (getattr(tr, "text", "") or "").strip()
