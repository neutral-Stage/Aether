"""Local on-device TTS — Kokoro-82M via the `kokoro` package.

Optional dependency, runtime-detected (needs espeak-ng for some voices);
callers fall back to macos/groq engines when unavailable.
"""
from __future__ import annotations

import io
import wave

SAMPLE_RATE = 24000  # Kokoro output rate


class LocalTTS:
    def __init__(self, voice: str = "af_heart") -> None:
        self.voice = voice
        self._pipeline = None

    def available(self) -> bool:
        try:
            import kokoro  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline

            self._pipeline = KPipeline(lang_code=self.voice[0])
        return self._pipeline

    def synthesize_bytes(self, text: str) -> bytes:
        """Full WAV (int16 mono 24kHz) for the given text."""
        import numpy as np

        pipeline = self._get_pipeline()
        chunks = [audio for _gs, _ps, audio in pipeline(text, voice=self.voice)]
        if not chunks:
            return b""
        pcm = np.concatenate([np.asarray(c) for c in chunks])
        pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm16.tobytes())
        return buf.getvalue()
