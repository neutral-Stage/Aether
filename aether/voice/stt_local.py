"""Local on-device STT — mlx-whisper or pywhispercpp (Metal-accelerated).

Optional dependencies, runtime-detected; callers fall back to cloud/typed
input when unavailable (same pattern as Playwright).
"""
from __future__ import annotations

from pathlib import Path

_BACKENDS = ("mlx_whisper", "pywhispercpp")


class LocalSTT:
    def __init__(self, model: str = "base") -> None:
        self.model = model
        self._backend: str | None = None
        self._impl = None

    def available(self) -> bool:
        return self._detect() is not None

    def _detect(self) -> str | None:
        if self._backend:
            return self._backend
        for name in _BACKENDS:
            try:
                __import__(name)
                self._backend = name
                return name
            except ImportError:
                continue
        return None

    def transcribe(self, path: str | Path) -> str:
        backend = self._detect()
        if backend is None:
            raise RuntimeError(
                "no local STT backend — pip install mlx-whisper or pywhispercpp"
            )
        if backend == "mlx_whisper":
            import mlx_whisper

            result = mlx_whisper.transcribe(str(path))
            return str(result.get("text", "")).strip()
        # pywhispercpp
        if self._impl is None:
            from pywhispercpp.model import Model

            self._impl = Model(self.model)
        segments = self._impl.transcribe(str(path))
        return " ".join(s.text.strip() for s in segments).strip()
