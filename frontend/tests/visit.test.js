import test from 'node:test'
import assert from 'node:assert/strict'
import { canCapture, hasConsent, visitSeconds, recordingSecondsLeft, bodyMassIndex } from '../src/visit.js'

test('15 минут отсчитываются от начала и не продлеваются паузой', () => {
  const encounter = { started_at: 1000, recording_deadline: 1900, paused_seconds: 50 }
  assert.equal(canCapture(encounter, { processing_consent: true }, 1899), true)
  assert.equal(canCapture(encounter, { processing_consent: true }, 1900), false)
  assert.equal(canCapture({ ...encounter, paused_at: 1200 }, { processing_consent: true }, 1300), false)
  assert.equal(recordingSecondsLeft(encounter, 1950), 0)
  assert.equal(visitSeconds(encounter, 1300), 250)
  assert.equal(visitSeconds({ ...encounter, paused_at: 1200 }, 1400), 150)
  assert.equal(visitSeconds({ ...encounter, ended_at: 1500 }, 1900), 450)
})

test('закрытый и чужой приём не разрешают новую запись; отдельные старые согласия не повышаются', () => {
  const encounter = { started_at: 1000, recording_deadline: 1900 }
  assert.equal(canCapture({ ...encounter, ended_at: 1100 }, { processing_consent: true }, 1200), false)
  assert.equal(canCapture({ ...encounter, read_only: true }, { processing_consent: true }, 1200), false)
  assert.equal(hasConsent({ recording_consent: true, cloud_consent: true }), false)
  assert.equal(hasConsent({ processing_consent: false, recording_consent: true, cloud_consent: true, cloud_audio_consent: true, openai_audio_consent: true }), false)
  assert.equal(hasConsent({ recording_consent: true, cloud_consent: true, cloud_audio_consent: true, openai_audio_consent: true }), true)
})

test('ИМТ рассчитывается только из числовых измерений роста и веса', () => {
  assert.equal(bodyMassIndex({ height: '158', weight: '78' }), '31.2')
  assert.equal(bodyMassIndex({ height: '158 см', weight: '78 кг' }), '31.2')
  assert.equal(bodyMassIndex({ height: '180', weight: '80,5' }), '24.8')
  assert.equal(bodyMassIndex({ height: '', weight: '80' }), '')
  assert.equal(bodyMassIndex({ height: '0', weight: '80' }), '')
  assert.equal(bodyMassIndex({ height: 'не измерен', weight: '80' }), '')
})


test('серверный запрет и неподтверждённое согласие блокируют запись несмотря на старую отметку', () => {
  const encounter = { started_at: 1000, recording_deadline: 1900, recording_allowed: true }
  assert.equal(canCapture(encounter, { processing_consent: true, ai_processing_allowed: false }, 1200), false)
  assert.equal(canCapture({ ...encounter, recording_allowed: false }, { processing_consent: true, ai_processing_allowed: true }, 1200), false)
  assert.equal(canCapture(encounter, { processing_consent: false, ai_processing_allowed: false }, 1200), false)
  assert.equal(canCapture(encounter, { processing_consent: true, ai_processing_allowed: true }, 1200), true)
})
