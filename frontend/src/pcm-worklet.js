// Сбор сэмплов вне главного потока: UI и сеть не прерывают микрофон.
class ConsultationPCM extends AudioWorkletProcessor {
  constructor() {
    super(); this.enabled = false; this.buffer = new Float32Array(sampleRate); this.used = 0
    this.port.onmessage = ({ data }) => {
      if (data.type === 'start') this.enabled = true
      if (data.type === 'pause') this.enabled = false
      if (data.type === 'flush') {
        this.enabled = false; this.send(); this.port.postMessage({ type: 'flushed' })
      }
    }
  }
  send() {
    if (!this.used) return
    const samples = this.buffer.slice(0, this.used)
    this.port.postMessage({ type: 'samples', samples, rate: sampleRate }, [samples.buffer]); this.used = 0
  }
  process(inputs) {
    const channel = inputs[0]?.[0]
    if (this.enabled && channel) {
      for (const sample of channel) {
        this.buffer[this.used++] = sample
        if (this.used === this.buffer.length) this.send()
      }
    }
    return true
  }
}
registerProcessor('consultation-pcm', ConsultationPCM)
