import test from 'node:test'
import assert from 'node:assert/strict'
import { chunker, wav } from '../src/audio-chunks.js'
import { liveUploader } from '../src/live-upload.js'
import { tones, recordingSounds } from '../src/recording-sounds.js'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

test('AudioWorklet не теряет хвост при flush и не пишет паузу', () => {
  let Processor
  const messages = []
  const context = { sampleRate: 16000, Float32Array,
    AudioWorkletProcessor: class { port = { postMessage: message => messages.push(message) } },
    registerProcessor: (name, type) => { Processor = type } }
  vm.runInNewContext(readFileSync(new URL('../src/pcm-worklet.js', import.meta.url), 'utf8'), context)
  const node = new Processor()
  const input = [[new Float32Array(128).fill(0.1)]]
  node.port.onmessage({ data: { type: 'start' } }); node.process(input)
  node.port.onmessage({ data: { type: 'pause' } }); node.process(input)
  node.port.onmessage({ data: { type: 'start' } }); node.process(input)
  node.port.onmessage({ data: { type: 'flush' } }); node.process(input)
  assert.equal(messages[0].samples.length, 256)
  assert.equal(messages[1].type, 'flushed')
})

test('непрерывный PCM закрывается на тихой секунде после 20 секунд и принудительно на 30', async () => {
  const chunks = [], buffer = chunker(blob => chunks.push(blob)), voice = new Float32Array(48000).fill(0.2), silence = new Float32Array(48000)
  for (let i = 0; i < 20; i++) buffer.push(voice, 48000)
  assert.equal(chunks.length, 0)
  buffer.push(silence, 48000)
  assert.equal(chunks[0].size, 44 + 21 * 32000)
  for (let i = 0; i < 30; i++) buffer.push(voice, 48000)
  assert.equal(chunks[1].size, 44 + 30 * 32000)
  buffer.push(voice.slice(0, 24000), 48000); buffer.flush(); buffer.flush()
  assert.equal(chunks.length, 3)
  assert.equal(chunks[2].size, 44 + 16000)
  const view = new DataView(await chunks[2].arrayBuffer())
  assert.equal(view.getUint32(24, true), 16000)
  assert.equal(view.getUint16(22, true), 1)
})

test('потеря подтверждения не удаляет фрагмент; повтор сохраняет номер и порядок', async () => {
  const calls = [], failures = []; let offline = true
  const upload = liveUploader(async (blob, sequence) => {
    calls.push(sequence)
    if (offline) throw new Error('offline')
    return { sequence }
  }, error => failures.push(error))
  upload.add(wav([new Int16Array(16)])); upload.add(wav([new Int16Array(32)]))
  await assert.rejects(upload.drain(), /offline/)
  assert.equal(upload.pending, 2); assert.equal(upload.acknowledged, 0)
  offline = false; await upload.drain()
  assert.equal(upload.pending, 0); assert.equal(upload.acknowledged, 2)
  assert.equal(calls.at(-2), 0); assert.equal(calls.at(-1), 1)
  assert.ok(failures.length)
})

test('звуки старта, остановки и тревоги имеют разные последовательности', async () => {
  const emitted = []
  globalThis.AudioContext = class {
    currentTime = 1
    resume() {} close() {}
    createOscillator() { const o = { frequency: {}, connect() {}, disconnect() {}, start(t) { emitted.push([o.frequency.value, t]) }, stop() {} }; return o }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {}, disconnect() {} } }
  }
  const sounds = recordingSounds()
  for (const kind of ['start', 'stop', 'alarm']) await sounds.play(kind)
  assert.equal(emitted.length, 8)
  assert.ok(tones.start[0][0] < tones.start[1][0])
  assert.ok(tones.stop[0][0] > tones.stop[1][0])
  assert.equal(tones.alarm.length, 4)
  sounds.close()
})
