import io
import wave
from pathlib import Path
from types import SimpleNamespace
from app.audio_privacy import mask_audio


def test_redacted_audio_preserves_timeline_and_mutes_padded_interval(monkeypatch):
    pcm = b'\x10\x10' * 64000  # 4 секунды mono 16kHz.
    monkeypatch.setattr('app.audio_privacy.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=pcm))
    masked = mask_audio(Path('synthetic.wav'), [{'start': 1, 'end': 2}], [0])
    with wave.open(io.BytesIO(masked)) as f:
        assert f.getnframes() == 64000
        assert f.getframerate() == 16000
        frames = f.readframes(64000)
    assert frames[:24000] == pcm[:24000]
    assert frames[24000:72000] == b'\x00' * 48000
    assert frames[72000:] == pcm[72000:]
