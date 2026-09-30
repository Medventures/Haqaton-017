"""Заглушение целых реплик с ПДн сохраняет исходную временную шкалу."""
import io
import subprocess
import wave
from pathlib import Path


def mask_audio(path, segments, indices):
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-t', '3600', '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:1'], capture_output=True, timeout=180, check=True)
    samples = bytearray(result.stdout)
    for index in indices:
        segment = segments[index]
        start = max(0, int((segment['start'] - .25) * 16000)) * 2
        end = min(len(samples), int((segment['end'] + .25) * 16000) * 2)
        if end > start:
            samples[start:end] = b'\x00' * (end - start)
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples)
    return out.getvalue()
