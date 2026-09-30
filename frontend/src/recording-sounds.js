export const tones = {
  start: [[660, 0, 0.10], [880, 0.12, 0.13]],
  stop: [[880, 0, 0.10], [440, 0.12, 0.16]],
  alarm: [[1046, 0, 0.18], [523, 0.22, 0.18], [1046, 0.44, 0.18], [523, 0.66, 0.30]],
}

export function recordingSounds() {
  let context
  const prime = async () => {
    try { context ||= new AudioContext(); await context.resume?.() } catch { /* Текстовое уведомление остаётся доступным. */ }
  }
  const play = async kind => {
    await prime()
    if (!context?.createOscillator) return
    try {
      const origin = context.currentTime
      for (const [frequency, delay, duration] of tones[kind]) {
        const oscillator = context.createOscillator(), gain = context.createGain()
        oscillator.frequency.value = frequency
        gain.gain.setValueAtTime(0, origin + delay)
        gain.gain.linearRampToValueAtTime(0.14, origin + delay + 0.015)
        gain.gain.exponentialRampToValueAtTime(0.001, origin + delay + duration)
        oscillator.connect(gain); gain.connect(context.destination)
        oscillator.onended = () => { oscillator.disconnect(); gain.disconnect() }
        oscillator.start(origin + delay); oscillator.stop(origin + delay + duration)
      }
    } catch { /* Отсутствие звукового устройства не должно прерывать запись. */ }
  }
  return { prime, play, close: () => { context?.close(); context = null } }
}
