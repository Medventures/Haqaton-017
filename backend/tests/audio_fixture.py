"""Короткая синтетическая тишина: допустимый WAV без персональных данных."""
import io
import wave


def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b'\x00\x00' * 800)
    return output.getvalue()
