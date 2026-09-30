import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { useRecorder } from '../src/recorder.js'

test('пауза, продолжение и ожидание последнего фрагмента при остановке', async () => {
  let instance, stopped = false
  const track = {stop() { stopped = true }}
  const stream = {getTracks: () => [track], getAudioTracks: () => [track]}
  globalThis.localStorage = {getItem: () => '', setItem() {}}
  Object.defineProperty(globalThis, 'navigator', {configurable:true, value:{mediaDevices:{getUserMedia:async()=>stream}}})
  globalThis.window = {addEventListener(){}, removeEventListener(){}}
  globalThis.MediaRecorder = window.MediaRecorder = class {
    static isTypeSupported() { return true }
    constructor() { instance=this; this.state='inactive'; this.mimeType='audio/webm' }
    start() { this.state='recording' }
    pause() { this.state='paused' }
    resume() { this.state='recording' }
    stop() { this.state='inactive'; setTimeout(()=>{this.ondataavailable({data:new Blob(['final chunk'])}); this.onstop()},5) }
  }
  globalThis.AudioContext = class {createAnalyser(){return {fftSize:0,frequencyBinCount:1,getByteFrequencyData(){}}} createMediaStreamSource(){return {connect(){}}} close(){} }
  globalThis.requestAnimationFrame = () => 1
  globalThis.cancelAnimationFrame = () => {}
  let recorder
  await renderToString(createSSRApp({setup(){recorder=useRecorder();return()=>null}}))
  await recorder.start()
  assert.equal(recorder.recording.value,true)
  recorder.pause(); assert.equal(instance.state,'paused'); assert.equal(recorder.paused.value,true)
  recorder.pause(); assert.equal(instance.state,'recording'); assert.equal(recorder.paused.value,false)
  const blob=await recorder.stop()
  assert.equal(await blob.text(),'final chunk')
  assert.equal(recorder.recording.value,false)
  assert.equal(stopped,true)
  recorder.clearAudio()
  assert.equal(recorder.audioBlob.value,null)
  let interrupted = false
  await recorder.start({ onInterrupted: () => { interrupted = true } })
  track.onended()
  await new Promise(resolve => setTimeout(resolve, 30))
  assert.equal(recorder.recording.value, false)
  assert.equal(interrupted, true)
  assert.match(recorder.recorderError.value, /Микрофон отключён/)
  assert.equal(await recorder.audioBlob.value.text(), 'final chunk')
  recorder.clearAudio()
})

