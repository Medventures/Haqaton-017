export function pcm16(samples, rate) {
  const count = Math.round(samples.length * 16000 / rate), result = new Int16Array(count)
  for (let i = 0; i < count; i++) {
    const from = Math.floor(i * rate / 16000), to = Math.min(samples.length, Math.max(from + 1, Math.floor((i + 1) * rate / 16000)))
    let value = 0
    for (let j = from; j < to; j++) value += samples[j]
    result[i] = Math.max(-1, Math.min(1, value / (to - from))) * 32767
  }
  return result
}

export function wav(parts) {
  const count = parts.reduce((n, part) => n + part.length, 0), buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer)
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)))
  text(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); text(8, 'WAVE'); text(12, 'fmt ')
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true)
  text(36, 'data'); view.setUint32(40, count * 2, true)
  let offset = 44
  for (const part of parts) for (const value of part) { view.setInt16(offset, value, true); offset += 2 }
  return new Blob([buffer], { type: 'audio/wav' })
}

export function chunker(emit) {
  let parts = [], length = 0
  const flush = () => {
    if (!length) return
    emit(wav(parts)); parts = []; length = 0
  }
  return {
    push(samples, rate) {
      const part = pcm16(samples, rate)
      parts.push(part); length += part.length
      const rms = Math.sqrt(part.reduce((sum, sample) => sum + sample * sample, 0) / (part.length || 1)) / 32768
      // Пакеты приходят раз в секунду; тихая секунда служит границей паузы.
      if (length >= 30 * 16000 || (length >= 20 * 16000 && rms < 0.012)) flush()
    }, flush,
  }
}
