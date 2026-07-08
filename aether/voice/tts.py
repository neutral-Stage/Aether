"""Text-to-speech — macOS `say`, Groq Orpheus TTS, or none."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class TTS:
    def __init__(
        self,
        engine: str = "macos",
        voice: str = "Samantha",
        model: str = "canopylabs/orpheus-v1-english",
        groq_api_key: str | None = None,
    ):
        self.engine = engine
        self.voice = voice
        self.model = model
        self.groq_api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        self._local = None  # lazy LocalTTS (Kokoro)

    def _local_tts(self):
        if self._local is None:
            from .tts_local import LocalTTS
            # config tts_voice is engine-specific; Kokoro voices look like af_heart
            voice = self.voice if self.voice.startswith(("af_", "am_", "bf_", "bm_")) else "af_heart"
            self._local = LocalTTS(voice=voice)
        return self._local

    def speak(self, text: str, blocking: bool = True) -> None:
        text = (text or "").strip()
        if not text or self.engine == "none":
            return
        if self.engine == "macos":
            self._say(text, blocking)
        elif self.engine == "groq":
            self._speak_groq(text, blocking)
        elif self.engine == "kokoro":
            if self._local_tts().available():
                self._play_bytes(self._local_tts().synthesize_bytes(text), blocking, text)
            else:
                self._say(text, blocking)  # graceful fallback to macOS say
        else:
            print(f"[TTS:{self.engine} unsupported] {text}")

    def synthesize_bytes(self, text: str) -> bytes:
        """Return raw audio bytes (WAV) for sidecar / Swift clients."""
        text = (text or "").strip()
        if not text or self.engine == "none":
            return b""
        if self.engine == "groq":
            return self._groq_audio_bytes(text)
        if self.engine == "kokoro":
            if not self._local_tts().available():
                raise RuntimeError("Kokoro not installed — pip install kokoro (needs espeak-ng)")
            return self._local_tts().synthesize_bytes(text)
        raise RuntimeError(f"TTS engine {self.engine!r} does not support byte synthesis")

    def synthesize_stream(self, text: str, chunk_size: int = 4096):
        """Yield audio chunks for streaming TTS (Phase 10)."""
        data = self.synthesize_bytes(text)
        if not data:
            return
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    def _say(self, text: str, blocking: bool) -> None:
        args = ["say"]
        if self.voice:
            args += ["-v", self.voice]
        args.append(text)
        try:
            if blocking:
                subprocess.run(args, check=False, timeout=120)
            else:
                subprocess.Popen(args)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS error: {e}] {text}")

    def _speak_groq(self, text: str, blocking: bool) -> None:
        try:
            self._play_bytes(self._groq_audio_bytes(text), blocking, text)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS groq error: {e}] {text}")

    def _play_bytes(self, data: bytes, blocking: bool, text: str) -> None:
        try:
            if not data:
                return
            path = Path(tempfile.gettempdir()) / f"aether-tts-{os.getpid()}.wav"
            path.write_bytes(data)
            args = ["afplay", str(path)]
            if blocking:
                subprocess.run(args, check=False, timeout=120)
            else:
                subprocess.Popen(args)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS error: {e}] {text}")

    def _groq_audio_bytes(self, text: str) -> bytes:
        from openai import OpenAI

        if not self.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set (see .env.example).")
        client = OpenAI(
            api_key=self.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
            response_format="wav",
        )
        return bytes(response.content)


if __name__ == "__main__":
    TTS().speak("Aether voice output is working.")
