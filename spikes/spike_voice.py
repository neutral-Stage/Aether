"""Spike 5 — voice round trip: listen (STT) -> echo back (TTS).

With OPENAI_API_KEY + mic deps it records and transcribes; otherwise it falls
back to typed input. Either way it speaks the result back via macOS `say`.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.core.config import load_config  # noqa: E402
from aether.voice.stt import STT  # noqa: E402
from aether.voice.tts import TTS  # noqa: E402

if __name__ == "__main__":
    cfg = load_config()
    tts = TTS(engine=cfg.tts, voice=cfg.tts_voice)
    stt = STT(engine=cfg.stt, model=cfg.stt_model,
              openai_api_key=cfg.openai_api_key,
              record_seconds=cfg.record_seconds)

    tts.speak("Voice spike ready. Say something after the prompt.")
    heard = stt.listen("Type or say something: ")
    if heard:
        tts.speak(f"I heard: {heard}")
    else:
        tts.speak("I did not catch anything.")
    print("Voice round trip complete.")
