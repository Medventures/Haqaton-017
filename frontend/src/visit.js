export const hasConsent = patient => patient?.ai_processing_allowed !== false && (patient?.processing_consent ?? !!(patient?.recording_consent && patient?.cloud_consent && patient?.cloud_audio_consent && patient?.openai_audio_consent))

export function visitSeconds(encounter, now) {
  return Math.max(0, Math.floor((encounter.ended_at || encounter.paused_at || now) - (encounter.started_at || encounter.created_at || now) - (encounter.paused_seconds || 0)))
}

export function recordingSecondsLeft(encounter, now) {
  return Math.max(0, Math.ceil((encounter.recording_deadline || encounter.capture_deadline || 0) - now))
}

export function canCapture(encounter, patient, now) {
  return hasConsent(patient) && encounter.recording_allowed !== false && !encounter.read_only && encounter.can_edit !== false && !encounter.ended_at && !encounter.paused_at && recordingSecondsLeft(encounter, now) > 0
}

export function formatDuration(value) {
  const seconds = Math.max(0, Math.floor(value))
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

export function bodyMassIndex(fields) {
  const height = measurement(fields.height, 'см|cm') / 100
  const weight = measurement(fields.weight, 'кг|kg')
  return height >= 0.3 && height <= 2.8 && weight > 0 && weight <= 700 ? (weight / height ** 2).toFixed(1) : ''
}

function measurement(value, units) {
  const match = String(value || '').trim().match(new RegExp(`^(\\d+(?:[.,]\\d+)?)\\s*(?:${units})?$`, 'i'))
  return match ? Number(match[1].replace(',', '.')) : NaN
}
