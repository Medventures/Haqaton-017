<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ArrowLeft, Mic, Square, Upload, Sparkles, Save, Check, Send, ShieldCheck, RefreshCw, Plus, Trash2, Headphones, Pause, Play, Clock3, FileText, X } from 'lucide-vue-next'
import { api } from './api'
import { useRecorder } from './recorder'
import { liveUploader } from './live-upload'
import { hasConsent, canCapture, visitSeconds, recordingSecondsLeft, formatDuration, bodyMassIndex } from './visit'
import DiagnosisPicker from './DiagnosisPicker.vue'
import ClinicalField from './ClinicalField.vue'
import './consultation.css'

const props = defineProps(['initial', 'patient', 'settings', 'doctor'])
const emit = defineEmits(['back', 'updated', 'open-previous'])
const e = ref(JSON.parse(JSON.stringify(props.initial))), error = ref(''), notice = ref(''), busy = ref(false), dirty = ref(false), tab = ref('transcript')
const rec = useRecorder()
const live = ref(props.initial.live_session || null), liveProgress = ref(null), liveError = ref(''), livePending = ref(0)
let delivery, liveTimer, retryTimer, liveFinishing = false, connectionAlarm = false, liveFinalizing = false
const { devices, selected, recording, paused, recorderError, seconds, audioUrl, audioBlob, level } = rec
const records = ref([]), revisions = ref([]), previous = ref([]), selectedRecord = ref(''), player = ref(null), sourceIndex = ref(-1), stage = ref(''), approvalChecked = ref(false)
const privacyDirty = ref(false), audioReviewed = ref(false), showMaskedAudio = ref(false), leaveDestination = ref(''), leaveDialog = ref(null), audioFileInput = ref(null)
const transcriptDirty = ref(false), transcriptOpen = ref(false)
const serverOffset = ref(props.initial.server_time ? props.initial.server_time - Date.now() / 1000 : 0), now = ref(Date.now() / 1000 + serverOffset.value)
const persisted = computed(() => e.value.persisted !== false)
const readOnly = computed(() => !!e.value.read_only || e.value.can_edit === false)
const processing = computed(() => e.value.status === 'processing')
const reviewed = computed(() => ['approved', 'exported'].includes(e.value.status) && !dirty.value)
const locked = computed(() => busy.value || processing.value || recording.value || !!live.value || readOnly.value)
const canGenerate = computed(() => props.settings.llm_configured && (e.value.transcript.length || Object.keys({ ...fields, ...vitals }).some(key => !key.startsWith('ai_') && e.value.fields[key]?.trim())))
const recordAllowed = computed(() => canCapture(e.value, props.patient, now.value))
const canTranscribe = computed(() => props.settings.asr_configured && hasConsent(props.patient))
const elapsed = computed(() => formatDuration(visitSeconds(e.value, now.value)))
const captureLeft = computed(() => recordingSecondsLeft(e.value, now.value))
const prior = computed(() => previous.value.find(x => x.id === e.value.previous_encounter_id) || previous.value.find(x => ['approved', 'exported'].includes(x.status) && (x.started_at || x.created_at) < (e.value.started_at || e.value.created_at)))
const speakers = computed(() => [...new Set(e.value.transcript.map(s => s.speaker))])
const speakerOptions = computed(() => [...new Set(['SPEAKER_00', 'SPEAKER_01', 'SPEAKER_02', ...speakers.value])])
const audioSource = computed(() => selectedRecord.value ? `/api/v1/encounters/${e.value.id}/recordings/${selectedRecord.value}/audio` : '')
const age = computed(() => { if (!props.patient.birth_date) return '—'; const birth = new Date(props.patient.birth_date), today = new Date(); return today.getFullYear() - birth.getFullYear() - (today < new Date(today.getFullYear(), birth.getMonth(), birth.getDate()) ? 1 : 0) })
const date = value => value ? new Date(value * 1000).toLocaleString('ru-RU') : '—'
const roles = { doctor: 'Врач', patient: 'Пациент', nurse: 'Медсестра', unknown: 'Роль не определена' }
const fields = { complaints: 'Жалобы', anamnesis: 'Анамнез заболевания', life_history: 'Анамнез жизни', allergies: 'Аллергии и реакции', medications: 'Принимаемые лекарства', chronic_conditions: 'Хронические заболевания', family_history: 'Наследственность', operations: 'Операции и перенесённые заболевания', habits: 'Привычки и факторы риска', examination: 'Объективные данные', investigations: 'Исследования и результаты', diagnosis: 'Диагноз врача', recommendations: 'Назначения и рекомендации', follow_up: 'План наблюдения', ai_conclusion: 'Архивная подсказка ИИ', ai_test_recommendations: 'Дополнительные анализы и обследования', ai_diagnosis_variants: 'Предварительные варианты диагноза' }
const vitals = { temperature: 'Температура, °C', blood_pressure: 'АД, мм рт. ст.', pulse: 'Пульс, уд/мин', respiratory_rate: 'ЧДД, в минуту', spo2: 'SpO₂, %', height: 'Рост, см', weight: 'Вес, кг' }
const groups = [{ title: 'Жалобы', keys: ['complaints'] }, { title: 'Анамнез', keys: ['anamnesis', 'life_history', 'allergies', 'medications', 'chronic_conditions', 'family_history', 'operations', 'habits'] }, { title: 'Объективные данные', keys: ['examination'] }, { title: 'Исследования', keys: ['investigations'] }]
const resultGroups = [{ title: 'Диагноз', keys: ['diagnosis'] }, { title: 'Назначения и наблюдение', keys: ['recommendations', 'follow_up'] }]
let pollTimer, clockTimer, captureToken = '', deadlineStopping = false, updateSerial = 0, currentJob = ['running', 'queued'].includes(props.initial.last_job?.state) ? props.initial.last_job.id : null, destroyed = false, returnFocus

async function run(fn) { error.value = ''; notice.value = ''; busy.value = true; try { await fn() } catch (err) { error.value = err.message } finally { busy.value = false } }
function update(value) {
  if (value.server_time) { serverOffset.value = value.server_time - Date.now() / 1000; now.value = value.server_time }
  updateSerial++; e.value = value; dirty.value = false; transcriptDirty.value = false; privacyDirty.value = false; audioReviewed.value = false; approvalChecked.value = false; emit('updated', value)
}
function lifecycleBody() { return { version: e.value.version, ...(persisted.value ? {} : { draft_token: e.value.draft_token }) } }
function changed(key) {
  if (key === 'diagnosis') e.value.fields.diagnosis_code = ''
  dirty.value = true; approvalChecked.value = false
  e.value.fields.reviewed_fields = (e.value.fields.reviewed_fields || []).filter(x => x !== key)
  e.value.fields.sources = (e.value.fields.sources || []).filter(x => x.field !== key)
}
function reviewField(key) { const list = e.value.fields.reviewed_fields || []; e.value.fields.reviewed_fields = list.includes(key) ? list.filter(x => x !== key) : [...list, key]; dirty.value = true; approvalChecked.value = false }
function sources(key) { return e.value.fields.sources?.find(x => x.field === key)?.segments || [] }
async function refreshArchive() {
  if (!persisted.value || readOnly.value) return
  const [audio, history] = await Promise.all([api(`/encounters/${e.value.id}/recordings`), api(`/encounters/${e.value.id}/history`)])
  records.value = audio; revisions.value = history
  if (!selectedRecord.value || !audio.some(x => x.id === selectedRecord.value)) selectedRecord.value = audio.find(x => x.available)?.id || ''
}
async function save() {
  if (privacyDirty.value) throw new Error('Сначала подтвердите изменения маскирования во вкладке «Маскирование»')
  update(await api(`/encounters/${e.value.id}`, { method: persisted.value ? 'PATCH' : 'PUT', body: { ...(persisted.value ? { version: e.value.version } : { draft_token: e.value.draft_token }), fields: e.value.fields, ...(!persisted.value || transcriptDirty.value ? { transcript: e.value.transcript } : {}), speaker_roles: e.value.speaker_roles, previous_encounter_id: e.value.previous_encounter_id } }))
  await refreshArchive(); notice.value = persisted.value ? 'Черновик сохранён. Его можно доработать в разделе «Мои приёмы».' : 'Лист пока пуст. Он сохранится после добавления данных.'
}
async function poll() {
  clearTimeout(pollTimer)
  const serial = updateSerial
  try {
    if (currentJob) {
      const job = await api(`/jobs/${currentJob}`); stage.value = job.stage || job.kind
      if (job.error) error.value = job.error
      if (['done', 'failed'].includes(job.state)) currentJob = null
    }
    const result = await api(`/encounters/${e.value.id}`)
    if (destroyed) return
    if (result.last_job?.error) error.value = result.last_job.error
    if (result.live_session?.state === 'failed') live.value = result.live_session
    if (liveFinalizing && result.status !== 'processing') {
      if (!result.last_job?.error && !recorderError.value && !result.live_session) rec.clearAudio()
      liveFinalizing = false
    }
    // Не перетираем локальные изменения поздним ответом предыдущего запроса.
    if (serial === updateSerial && !dirty.value && !privacyDirty.value) update(result)
    if (result.status === 'processing') pollTimer = setTimeout(poll, 2000)
    else { await refreshArchive(); notice.value = result.last_job?.error ? 'Запись сохранена. Обработка требует повторной попытки.' : 'Обработка завершена. Проверьте расшифровку и лист консультации.' }
  } catch (err) { error.value = err.message; if (!destroyed) pollTimer = setTimeout(poll, 5000) }
}
async function queue(path, body) {
  const result = await api(`/encounters/${e.value.id}/${path}`, { method: 'POST', body })
  currentJob = result.job_id; e.value.persisted = true; e.value.status = 'processing'; stage.value = 'queued'
  clearTimeout(pollTimer); pollTimer = setTimeout(poll, 1000)
}
async function upload(blob) {
  if (!blob?.size) throw new Error('Запись пуста')
  if (!canTranscribe.value) throw new Error('Проверьте настройку ASR и согласие пациента в его карте')
  if (dirty.value) await save()
  const data = new FormData()
  data.append('file', blob, blob.name || 'consultation.' + (blob.type.includes('mp4') ? 'mp4' : 'webm')); data.append('analyze', 'true')
  if (!persisted.value) data.append('draft_token', e.value.draft_token)
  if (captureToken) data.append('capture_token', captureToken)
  await queue('audio', data)
  rec.clearAudio(); captureToken = ''
  notice.value = 'Запись сохранена на сервере. Распознавание и анализ запущены.'
}
async function pollLive() {
  clearTimeout(liveTimer); liveTimer = null
  if (!live.value) return
  const id = live.value.id
  try {
    const result = await api(`/encounters/${e.value.id}/live/${id}`)
    if (live.value?.id !== id || liveFinishing || destroyed) return
    liveProgress.value = result
    if (!dirty.value) { update(result.encounter); e.value.transcript = result.transcript }
    if (result.error) liveError.value = result.error
    if (['done', 'failed'].includes(result.state)) {
      if (result.state === 'done') { live.value = null; liveProgress.value = null; await refreshArchive(); return }
    }
  } catch (err) { if (delivery?.acknowledged) liveError.value = err.message }
  if (!destroyed && live.value) liveTimer = setTimeout(pollLive, 2000)
}
async function completeLive(interrupted = false) {
  if (liveFinishing || !live.value) return
  liveFinishing = true
  try {
    await delivery?.drain()
    livePending.value = delivery?.pending || 0
    const count = delivery?.acknowledged || liveProgress.value?.received || live.value.count || 0
    if (!count) throw new Error('Нет сохранённых фрагментов. Скачайте резервную запись.')
    await queue(`live/${live.value.id}/finish`, { count, interrupted })
    liveFinalizing = true
    clearTimeout(liveTimer); liveTimer = null; clearInterval(retryTimer)
    live.value = null; liveProgress.value = null; liveError.value = ''
    captureToken = ''
    notice.value = 'Фрагменты сохранены. Обрабатываем остаток и выполняем итоговый анализ.'
  } finally { liveFinishing = false }
}
async function startRecording() {
  if (!recordAllowed.value) throw new Error('Новая запись недоступна. Проверьте согласие и время приёма.')
  await rec.primeSounds()
  if (audioBlob.value) throw new Error('Сначала отправьте выбранную запись или удалите её')
  if (dirty.value) await save()
  const lease = await api(`/encounters/${e.value.id}/capture-lease`, { method: 'POST', body: lifecycleBody() })
  captureToken = lease.capture_token
  const sessionId = crypto.randomUUID()
  live.value = { id: sessionId, count: 0 }; liveProgress.value = null; liveError.value = ''; connectionAlarm = false
  delivery = liveUploader(async (blob, sequence) => {
    const form = new FormData(); form.append('file', blob, 'fragment.wav'); form.append('sequence', String(sequence))
    if (e.value.draft_token) form.append('draft_token', e.value.draft_token)
    form.append('capture_token', captureToken)
    const result = await api(`/encounters/${e.value.id}/live/${sessionId}/parts`, { method: 'POST', body: form, signal: AbortSignal.timeout(30000) })
    e.value.persisted = true; connectionAlarm = false; liveError.value = ''
    if (!liveTimer) liveTimer = setTimeout(pollLive, 300)
    return result
  }, message => {
    liveError.value = message + ' Фрагменты остаются в браузере; отправка будет повторена.'
    if (!connectionAlarm) { rec.alarm(); connectionAlarm = true }
  })
  try {
    await rec.start({ onChunk: blob => { delivery.add(blob); livePending.value = delivery.pending },
      onInterrupted: () => { void run(() => completeLive(true)) } })
  } catch (err) { live.value = null; throw err }
  transcriptOpen.value = true
  retryTimer = setInterval(() => { if (delivery?.pending) void delivery.retry(); livePending.value = delivery?.pending || 0 }, 5000)
  if (Date.now() / 1000 + serverOffset.value >= e.value.recording_deadline) {
    await rec.stop()
    if (delivery.pending || delivery.acknowledged) await completeLive(true)
    else { live.value = null; clearInterval(retryTimer); clearTimeout(liveTimer); liveTimer = null }
    throw new Error('Время для записи истекло во время запроса микрофона. Резервную запись можно скачать.')
  }
}
async function finishDialogue() { const blob = await rec.stop(); if (live.value) await completeLive(!!recorderError.value); else await upload(blob) }
function chooseFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''; if (!file || !recordAllowed.value) return
  if (file.size > 80 * 1024 * 1024) { error.value = 'Максимальный размер файла — 80 МБ'; return }
  const extensions = { mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4', mp4: 'audio/mp4', webm: 'audio/webm', ogg: 'audio/ogg', flac: 'audio/flac' }
  const mime = extensions[file.name.split('.').pop().toLowerCase()]
  if (!mime) { error.value = 'Выберите WAV, MP3, M4A, MP4, WebM, OGG или FLAC'; return }
  captureToken = ''; rec.setAudio(new Blob([file], { type: mime })); notice.value = `Выбрано: ${file.name}. Нажмите «Распознать и заполнить».`
}
async function preserveChanges() {
  if (privacyDirty.value) await privacy()
  if (recording.value) await finishDialogue()
  else if (live.value) await completeLive(!!recorderError.value)
  else if (audioBlob.value) await upload(audioBlob.value)
  if (dirty.value) await save()
}
async function finishVisit() {
  await preserveChanges()
  update(await api(`/encounters/${e.value.id}/finish`, { method: 'POST', body: lifecycleBody() }))
  if (e.value.discarded) { emit('back', { encounter: e.value, action: 'finish', discarded: true, destination: 'patient' }); return }
  notice.value = 'Приём завершён. Новая запись закрыта; лист можно доработать и проверить позже.'
}
async function resumeVisit() { update(await api(`/encounters/${e.value.id}/resume`, { method: 'POST', body: lifecycleBody() })); notice.value = 'Приём продолжен. Окно записи отсчитывается от первоначального начала.' }
async function generate(target = 'all') { if (privacyDirty.value) throw new Error('Подтвердите изменения маскирования'); if (dirty.value) await save(); await queue('generate', { version: e.value.version, target }) }
async function retryRecording(record) {
  if (privacyDirty.value) throw new Error('Сначала подтвердите маскирование')
  if (dirty.value) await save()
  await queue(`recordings/${record.id}/transcribe`, { version: e.value.version, analyze: true })
}
async function privacy() {
  if (dirty.value) throw new Error('Сначала сохраните правки расшифровки и полей')
  update(await api(`/encounters/${e.value.id}/privacy-review`, { method: 'POST', body: { version: e.value.version, segments: e.value.redacted_transcript } })); notice.value = 'Маскирование проверено'
}
async function approve() {
  if (!approvalChecked.value) throw new Error('Подтвердите проверку результата')
  if (dirty.value) await save()
  if (!persisted.value) throw new Error('Добавьте сведения о консультации')
  if (!e.value.ended_at) await finishVisit()
  update(await api(`/encounters/${e.value.id}/approve`, { method: 'POST', body: { version: e.value.version } })); await refreshArchive(); notice.value = 'Проверено врачом. Результат доступен МИС через API.'
}
async function rejectDraft() {
  if (!window.confirm('Очистить поля черновика? Сохранённые аудио, расшифровка и предыдущие версии останутся в архиве.')) return
  for (const key of Object.keys(e.value.fields)) { if (['visit_type', 'visit_format'].includes(key)) continue; e.value.fields[key] = Array.isArray(e.value.fields[key]) ? [] : '' }
  dirty.value = true; await save()
}
function selectDiagnosis(item) { changed('diagnosis'); e.value.fields.diagnosis_code = item.code; e.value.fields.diagnosis = item.name }
async function showSource(index) {
  transcriptOpen.value = true
  tab.value = 'transcript'; sourceIndex.value = index
  const time = e.value.transcript[index]?.start
  const record = records.value.find(record => record.available && time >= (record.timeline_offset || 0) &&
    (!record.duration || time < (record.timeline_offset || 0) + record.duration))
  if (record) selectedRecord.value = record.id
  await nextTick(); document.getElementById('segment-' + index)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (player.value && record && Number.isFinite(time)) player.value.currentTime = Math.max(0, time - (record.timeline_offset || 0))
}
function transcriptChanged() { dirty.value = true; transcriptDirty.value = true; approvalChecked.value = false; e.value.fields.reviewed_fields = [] }
function addSegment() { e.value.transcript.push({ speaker: 'SPEAKER_00', start: 0, end: 0, text: '' }); e.value.speaker_roles.SPEAKER_00 ||= 'unknown'; transcriptChanged() }
function leave(action, destination) {
  leaveDestination.value = ''
  if (destination.startsWith('previous:')) emit('open-previous', destination.slice(9))
  else emit('back', { encounter: e.value, action, discarded: !!e.value.discarded, destination })
}
async function requestLeave(destination = 'patient') {
  if (readOnly.value || (e.value.ended_at && !dirty.value && !privacyDirty.value && !audioBlob.value)) { leave('back', destination); return }
  leaveDestination.value = destination; returnFocus = document.activeElement
  await nextTick(); leaveDialog.value?.querySelector('button')?.focus()
}
function cancelLeave() { leaveDestination.value = ''; returnFocus?.focus() }
async function confirmLeave(action) {
  const destination = leaveDestination.value
  await preserveChanges()
  if (!e.value.ended_at) update(await api(`/encounters/${e.value.id}/${action === 'pause' ? 'pause' : 'finish'}`, { method: 'POST', body: lifecycleBody() }))
  leave(action, destination)
}
function trapFocus(event) {
  if (event.key !== 'Tab') return
  const buttons = [...leaveDialog.value.querySelectorAll('button:not(:disabled)')], first = buttons[0], last = buttons.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
const unload = event => { if (dirty.value || privacyDirty.value) { event.preventDefault(); event.returnValue = '' } }
defineExpose({ requestLeave })
onMounted(async () => {
  clockTimer = setInterval(() => {
    now.value = Date.now() / 1000 + serverOffset.value
    if (recording.value && captureLeft.value <= 0 && !deadlineStopping && !busy.value) {
      deadlineStopping = true
      run(async () => { await finishDialogue(); notice.value = '15 минут истекли. Запись остановлена и отправлена на обработку.' }).finally(() => { deadlineStopping = false })
    }
  }, 500)
  window.addEventListener('beforeunload', unload)
  if (live.value) liveTimer = setTimeout(pollLive, 100)
  try { await refreshArchive(); previous.value = (await api(`/patients/${props.patient.id}/encounters`)).filter(x => x.id !== e.value.id); if (processing.value) poll() } catch (err) { error.value = err.message }
})
onBeforeUnmount(() => { destroyed = true; clearTimeout(liveTimer); clearInterval(retryTimer); clearTimeout(pollTimer); clearInterval(clockTimer); window.removeEventListener('beforeunload', unload) })
</script>

<template>
  <button class="text-button back" @click="requestLeave()"><ArrowLeft :size="16"/>К карте пациента</button>
  <div class="page-heading consultation-heading"><div><span class="eyebrow">SMART CONSULT · ЛИСТ КОНСУЛЬТАЦИИ</span><h1>{{ e.fields.visit_type === 'repeat' ? 'Повторный приём' : 'Первичный приём' }}</h1><p class="muted">{{ e.fields.visit_format === 'remote' ? 'Онлайн-консультация' : 'Очная консультация специалиста' }}</p></div><span class="badge" :class="e.status">{{ { draft: 'Черновик', ready: 'Требует проверки', processing: 'Обработка', approved: 'Проверено врачом', exported: 'Передан в МИС' }[e.status] }}</span></div>
  <section class="patient-strip"><strong>{{ patient.name }}</strong><span>Возраст: {{ age }}</span><span>ИИН: ••••••••{{ patient.iin?.slice(-4) }}</span><span>{{ date(e.started_at || e.created_at) }}</span><span>Врач: {{ e.physician_name || doctor?.name }}</span></section>
  <section class="visit-toolbar panel"><div class="visit-timer"><Clock3 :size="20"/><strong>{{ elapsed }}</strong><span>{{ e.ended_at ? 'Приём завершён' : e.paused_at ? 'Приём приостановлен' : 'Приём идёт' }}</span><small v-if="e.ended_at">Завершён: {{ date(e.ended_at) }}</small></div><div class="visit-toolbar-actions"><a v-if="persisted && !dirty && !processing" class="secondary pdf-link" :href="'/api/v1/encounters/'+e.id+'/pdf'" target="_blank" rel="noopener"><FileText :size="17"/>Открыть PDF</a><template v-if="!readOnly && !e.ended_at"><button v-if="e.paused_at" class="primary" :disabled="busy || processing" @click="run(resumeVisit)"><Play :size="17"/>Продолжить приём</button><button class="primary" :disabled="busy" @click="run(finishVisit)"><Square :size="16"/>Завершить приём</button></template></div></section>
  <div v-if="readOnly" class="alert">Проверенный лист другого врача. Доступен просмотр заключения и PDF.</div>
  <div v-else class="consent-strip"><ShieldCheck :size="18"/><strong>Обработка с ИИ:</strong><span :class="hasConsent(patient) ? 'consent-ok' : 'consent-no'">{{ hasConsent(patient) ? 'Согласие получено' : 'Без согласия · ручное заполнение' }}</span><span v-if="!e.ended_at && !e.paused_at && hasConsent(patient)">Новая запись: {{ captureLeft ? 'ещё ' + formatDuration(captureLeft) : 'время истекло' }}</span></div>
  <div v-if="error || recorderError" class="alert error" role="alert">{{ error || recorderError }}</div><div v-if="notice" class="alert success" role="status">{{ notice }}</div>
  <div v-if="privacyDirty" class="alert warning">Подтвердите изменения во вкладке «Маскирование», затем продолжайте редактирование листа.</div>
  <div v-if="processing" class="alert processing-alert"><RefreshCw :size="19" class="spin"/>{{ { queued: 'Запись в очереди', transcribing: 'Распознаём речь и разделяем голоса', generating: 'Заполняем поля и готовим заключение', generate: 'Готовим заключение' }[stage] || 'Обрабатываем приём' }}. Результат сохранится и после выхода из листа.</div>
  <div class="consultation-workspace" :class="{ 'document-only': readOnly }">
    <aside v-if="!readOnly" class="consultation-left">
      <section class="panel transcript-panel"><div class="section-title"><h3><Mic :size="20"/>Разговор</h3><span class="hint">{{ e.transcript.length }} реплик</span></div>
        <div v-if="recordAllowed || recording" class="conversation-recording">
          <div v-if="!recording" class="recording-privacy-notice" role="note" aria-labelledby="recording-privacy-title">
            <strong id="recording-privacy-title">Перед записью — напомните о конфиденциальности</strong>
            <p>Попросите участников не называть ФИО, ИИН, телефон, точный адрес и другие данные, по которым можно узнать человека. Обсуждайте приём обезличенно: «пациент», «врач», «родственник» — и только необходимые медицинские сведения.</p>
            <p v-if="settings.asr_provider === 'openai'">OpenAI получает исходное аудио до маскирования. Автоматическое обезличивание после распознавания может пропустить персональные данные.</p>
            <p v-else>Автоматическое маскирование может пропустить персональные данные. Проверьте текст перед передачей облачной модели.</p>
            <small>Напоминание не заменяет согласие пациента. Перед загрузкой готовой записи также проверьте её содержимое.</small>
          </div>
          <div class="recording-meter"><span class="record-clock">{{ formatDuration(seconds) }}</span><span class="recording-status" :class="{ active: recording && !paused, paused: paused }">{{ recording ? (paused ? 'Запись приостановлена' : 'Идёт запись') : 'Готово к записи' }}</span><div class="record-wave" aria-hidden="true"><i v-for="n in 24" :key="n" :style="{ height: (recording && !paused ? 5 + level * (15 + n % 5 * 13) : 4 + n % 4 * 3) + 'px' }"></i></div></div>
          <div class="record-buttons"><template v-if="recording"><button class="secondary" :disabled="busy" @click="rec.pause"><component :is="paused ? Play : Pause" :size="16"/>{{ paused ? 'Продолжить запись' : 'Пауза' }}</button><button class="primary" :disabled="busy" @click="run(finishDialogue)"><Square :size="16"/>Закончить диалог приёма</button></template><button v-else class="primary full" :disabled="locked || !!audioBlob" @click="run(startRecording)"><Mic :size="17"/>Начать запись</button></div>
          <p class="hint">Звуки: два восходящих тона — начало, нисходящих — окончание; повторяющийся сигнал — обрыв. Проверьте громкость устройства.</p>
          <details class="microphone-settings"><summary>Источник микрофона</summary><label>Устройство<select v-model="selected" :disabled="recording || busy" @change="rec.saveDevice"><option value="">Системный микрофон</option><option v-for="d in devices" :key="d.deviceId" :value="d.deviceId">{{ d.label || 'Микрофон' }}</option></select></label><button class="text-button" :disabled="recording || busy" @click="run(rec.discover)"><Headphones :size="15"/>Разрешить микрофон и обновить список</button><p class="hint">Выбор устройства сохраняется в этом браузере.</p></details>
          <div class="audio-upload-row"><button type="button" class="secondary" :disabled="locked || !!audioBlob" @click="audioFileInput?.click()"><Upload :size="18"/>Загрузить аудиофайл</button><span class="hint">WAV, MP3, M4A, MP4, WebM, OGG, FLAC · до 80 МБ</span><input ref="audioFileInput" class="audio-file-input" type="file" accept=".wav,.mp3,.m4a,.mp4,.webm,.ogg,.flac" :disabled="locked || !!audioBlob" aria-label="Загрузить запись приёма" @change="chooseFile"></div><p class="hint">Новая запись доступна в первые 15 минут приёма.</p>
        </div>
        <p v-else class="capture-closed">{{ e.ended_at ? 'Приём завершён. Новая запись недоступна. Сохранённые записи можно прослушать ниже.' : e.paused_at ? 'Возобновите приём, чтобы продолжить работу с записью.' : !hasConsent(patient) ? 'Нет действующего согласия на обработку с ИИ. Приостановите приём и оформите согласие с ЭЦП пациента в его карте. До подписания заполните лист вручную.' : '15 минут для новой записи истекли. Продолжайте заполнять лист вручную.' }}</p>
        <div v-if="audioUrl && !recording" class="uploaded-audio"><audio :src="audioUrl" controls/><button class="primary full" :disabled="busy || processing || !canTranscribe || !!e.ended_at" @click="run(() => live ? completeLive(!!recorderError) : upload(audioBlob))">Распознать и заполнить</button><a class="text-button" :href="audioUrl" download="consultation-audio">Скачать выбранную запись</a><button class="text-button" :disabled="locked" @click="rec.clearAudio(); captureToken = ''">Удалить выбранную запись</button></div>
        <div v-if="live || liveProgress" class="live-progress" role="status"><strong>Предварительная расшифровка по мере разговора</strong><p>Сохранено {{ liveProgress?.received || delivery?.acknowledged || 0 }} фрагментов · распознано {{ liveProgress?.completed || 0 }} · ожидают отправки {{ livePending }}</p><p class="hint">Фрагменты 20–30 секунд, черновик обновляется примерно раз в минуту. Номера голосов независимы между фрагментами; роли определяет ИИ, их проверяет врач.</p><a v-if="live && (liveProgress?.received || delivery?.acknowledged || live.count)" class="text-button" :href="`/api/v1/encounters/${e.id}/live/${live.id}/audio`" download>Скачать сохранённые на сервере фрагменты</a><button v-if="!recording" class="secondary" :disabled="busy || processing" @click="run(() => completeLive(true))">Завершить обработку сохранённых фрагментов</button></div>
        <div v-if="liveError" class="alert error" role="alert">{{ liveError }}</div>
        <details class="transcript-disclosure" :open="transcriptOpen" @toggle="transcriptOpen = $event.target.open"><summary>Расшифровка диалога <span class="hint">{{ e.transcript.length }} реплик · маскирование и архив</span></summary>
        <div class="tabs compact-tabs"><button :class="{ active: tab === 'transcript' }" @click="tab = 'transcript'">Расшифровка</button><button :class="{ active: tab === 'privacy' }" @click="tab = 'privacy'">Маскирование</button><button :class="{ active: tab === 'archive' }" @click="tab = 'archive'">Архив</button></div>
        <div v-if="audioSource" class="archive-player"><label>Сохранённая запись<select v-model="selectedRecord"><option v-for="r in records.filter(x => x.available)" :key="r.id" :value="r.id">{{ date(r.created_at) }}</option></select></label><audio ref="player" :src="audioSource" controls preload="metadata"/></div>
        <template v-if="tab === 'transcript'"><div v-if="speakers.length" class="speaker-roles"><label v-for="s in speakers" :key="s"><span>Говорящий {{ Number(s.split('_')[1]) + 1 }}</span><select v-model="e.speaker_roles[s]" :disabled="locked || privacyDirty" @change="transcriptChanged"><option v-for="(label, role) in roles" :key="role" :value="role">{{ label }}</option></select></label></div><div v-if="!e.transcript.length" class="empty"><Mic :size="30"/><p>После распознавания здесь появятся реплики и роли участников.</p></div>
          <div class="segments"><article v-for="(s, i) in e.transcript" :id="'segment-' + i" :key="i" class="segment" :class="{ 'source-highlight': sourceIndex === i }"><div class="segment-meta"><span class="speaker-dot" :class="e.speaker_roles[s.speaker]"></span><select v-model="s.speaker" aria-label="Говорящий" :disabled="locked || privacyDirty" @change="transcriptChanged"><option v-for="sp in speakerOptions" :key="sp" :value="sp">{{ roles[e.speaker_roles[sp]] || 'Говорящий' }} · {{ Number(sp.split('_')[1]) + 1 }}</option></select><button class="text-button" @click="showSource(i)">{{ formatDuration(s.start) }}</button><button class="icon-button" aria-label="Удалить реплику" :disabled="locked || privacyDirty" @click="e.transcript.splice(i, 1); e.fields.sources = []; transcriptChanged()"><Trash2 :size="13"/></button></div><textarea v-model="s.text" aria-label="Текст реплики" :disabled="locked || privacyDirty" rows="3" @input="transcriptChanged"></textarea></article></div><button class="text-button add-segment" :disabled="locked || privacyDirty" @click="addSegment"><Plus :size="15"/>Добавить реплику вручную</button></template>
        <template v-else-if="tab === 'privacy'"><p class="privacy-note">Проверьте имена, ИИН, телефоны и адреса. Автоматическое маскирование может пропустить сведения.</p><p v-if="dirty" class="hint">Сначала сохраните изменения листа.</p><div v-for="(s, i) in e.redacted_transcript" :key="i" class="segment"><textarea v-model="s.text" aria-label="Обезличенная реплика" :disabled="locked || dirty" rows="3" @input="privacyDirty = true"></textarea><button v-if="hasConsent(patient)" class="text-button" :disabled="locked || dirty || privacyDirty" @click="run(() => queue('mute-audio', { version: e.version, segment_indices: [i] }))">Заглушить реплику в копии аудио</button></div><button class="secondary full" :disabled="locked || dirty || !e.redacted_transcript.length" @click="run(privacy)">Подтвердить маскирование</button><div v-if="records.length" class="masked-audio-box"><button class="text-button" @click="showMaskedAudio = !showMaskedAudio">Прослушать маскированную копию</button><audio v-if="showMaskedAudio" :key="e.version" :src="'/api/v1/encounters/' + e.id + '/masked-audio'" controls/><template v-if="settings.cloud_asr_configured"><label class="check"><input v-model="audioReviewed" type="checkbox">Проверено отсутствие персональных данных в аудио</label><button class="secondary" :disabled="locked || !audioReviewed || !e.privacy_reviewed || !hasConsent(patient)" @click="run(() => queue('cloud-asr', { version: e.version, audio_reviewed: true }))">Уточнить распознавание</button></template></div></template>
        <template v-else><div class="archive-list"><p class="hint">Исходные записи, расшифровки и версии заключений сохраняются зашифрованно.</p><details v-for="r in records" :key="r.id"><summary>Аудио · {{ date(r.created_at) }} · {{ r.available ? 'Сохранено' : 'Недоступно' }}</summary><button class="secondary" :disabled="locked || !r.available || !canTranscribe" @click="run(() => retryRecording(r))">Повторно распознать сохранённую запись</button><p v-if="r.error" class="alert error">{{ r.error }}</p><p v-for="(s, i) in r.transcript" :key="i"><b>{{ roles[r.speaker_roles[s.speaker]] || s.speaker }}:</b> {{ s.text }}</p></details><details v-for="revision in revisions" :key="revision.id"><summary>Версия {{ revision.version }} · {{ date(revision.created_at) }} · {{ { approve: 'Проверка врачом', edit: 'Правка', generate: 'Заключение ИИ', transcribe: 'Распознавание', finish: 'Завершение приёма', before_edit: 'До правки', before_generate: 'До генерации', before_transcribe: 'До распознавания' }[revision.action] || revision.action }}</summary><template v-for="(label, key) in fields" :key="key"><p v-if="revision.snapshot.fields[key]"><b>{{ label }}:</b> {{ revision.snapshot.fields[key] }}</p></template></details></div></template>
        <div class="transcript-footer"><button class="secondary full" :disabled="locked || !canGenerate" @click="run(() => generate())"><Sparkles :size="17"/>Повторить анализ и заполнение</button></div>
        </details>
      </section>
      <section v-if="prior" class="panel clinical-context"><h3>Предыдущий приём</h3><template v-if="prior"><p class="hint">{{ date(prior.started_at || prior.created_at) }} · {{ prior.physician_name || 'Врач' }}</p><p><b>Аллергии:</b> {{ prior.fields.allergies || 'Не уточнено' }}</p><p><b>Лекарства:</b> {{ prior.fields.medications || 'Не уточнено' }}</p><p><b>Хронические заболевания:</b> {{ prior.fields.chronic_conditions || 'Не уточнено' }}</p><button class="text-button" @click="requestLeave('previous:' + prior.id)">Открыть лист консультации ↗</button></template><p v-else class="hint">Предыдущих доступных заключений нет.</p></section>
    </aside>
    <section class="panel consultation-document" :inert="privacyDirty || undefined"><div class="document-heading"><h3>Консультация специалиста</h3><div class="visit-selectors"><label>Вид<select v-model="e.fields.visit_type" :disabled="locked" @change="dirty = true"><option value="primary">Первичная</option><option value="repeat">Повторная</option></select></label><label>Формат<select v-model="e.fields.visit_format" :disabled="locked" @change="dirty = true"><option value="in_person">Очная</option><option value="remote">Онлайн</option></select></label></div></div>
      <label v-if="e.fields.visit_type === 'repeat'" class="previous-selector">Связь с предыдущим приёмом<select v-model="e.previous_encounter_id" :disabled="locked" @change="dirty = true"><option :value="null">Без привязки</option><option v-for="item in previous" :key="item.id" :value="item.id">{{ date(item.started_at || item.created_at) }} · {{ item.physician_name || 'Консультация' }}</option></select></label>
      <button v-if="readOnly && e.previous_encounter_id" class="text-button" @click="requestLeave('previous:' + e.previous_encounter_id)">Открыть предыдущий приём ↗</button>
      <div class="document-hint"><ShieldCheck :size="16"/>Пустое поле означает «не уточнено». Диагноз и назначения подтверждает врач.</div>
      <section v-for="(group, index) in groups" :key="group.title" class="clinical-section"><h3><span>{{ String(index + 1).padStart(2, '0') }}</span>{{ group.title }}</h3><DiagnosisPicker v-if="group.title === 'Диагноз'" :code="e.fields.diagnosis_code" :disabled="locked" @select="selectDiagnosis"/><div :class="{ 'clinical-two-columns': group.title === 'Анамнез' }"><ClinicalField v-for="key in group.keys" :key="key" v-model="e.fields[key]" :name="key" :label="fields[key]" :disabled="locked" :regeneratable="!readOnly && canGenerate && key !== 'diagnosis'" @regenerate="run(() => generate(key))" :sources="sources(key)" :reviewed="reviewed || e.fields.reviewed_fields?.includes(key)" @update:model-value="changed(key)" @source="showSource" @review="reviewField(key)"/></div><div v-if="group.title === 'Объективные данные'" class="vitals-grid"><label v-for="(label, key) in vitals" :key="key">{{ label }}<input v-model="e.fields[key]" :disabled="locked" placeholder="Не измерено" @input="changed(key)"><button v-if="!readOnly && canGenerate" class="text-button" :disabled="locked" :aria-label="'Перегенерировать: ' + label" @click="run(() => generate(key))">↻ Дополнить с ИИ</button><button v-if="sources(key).length" class="text-button" @click="showSource(sources(key)[0])">Источник ↗</button></label><label>ИМТ, кг/м²<input :value="bodyMassIndex(e.fields)" readonly placeholder="Нужны рост и вес" aria-label="Индекс массы тела"></label></div></section>
    </section>
    <section class="panel ai-advisory" aria-labelledby="ai-advisory-heading">
      <div class="document-heading"><h3 id="ai-advisory-heading"><Sparkles :size="20"/>Подсказки ИИ для врача</h3><span class="badge">Не входит в PDF и МИС</span></div>
      <div class="ai-advisory-content"><p class="ai-conclusion-notice">{{ e.ai_notice?.disclaimer || 'Подсказки ИИ не являются диагнозом или назначением. Не включаются в PDF и не передаются в МИС. Окончательное решение принимает врач.' }}</p>
        <p class="hint">Исправьте поля листа и подсказки перед повторным анализом. ИИ учтёт все правки; варианты диагноза дополняются, диагноз врача сохраняется.</p>
        <button v-if="!readOnly" class="secondary" :disabled="locked || !canGenerate" @click="run(() => generate())"><RefreshCw :size="16"/>Перегенерировать все ответы с учётом правок</button>
        <div class="ai-advisory-grid"><article v-for="key in ['ai_test_recommendations', 'ai_diagnosis_variants']" :key="key"><label :for="'field-' + key"><h4>{{ key === 'ai_test_recommendations' ? 'Рекомендации по дополнительным анализам и обследованиям' : 'Предварительные варианты диагноза на основе анамнеза' }}</h4></label><textarea :id="'field-' + key" v-model="e.fields[key]" :disabled="locked" rows="8" placeholder="Появятся после анализа. Можно внести уточнения перед перегенерацией." @input="changed(key)"></textarea><button v-if="!readOnly" class="text-button" :disabled="locked || !canGenerate" @click="run(() => generate(key))"><RefreshCw :size="15"/>Перегенерировать: {{ fields[key] }}</button><ul v-if="key === 'ai_diagnosis_variants' && e.fields.diagnosis_suggestions?.length"><li v-for="item in e.fields.diagnosis_suggestions" :key="item.code"><strong>{{ item.code }} · {{ item.name }}</strong><p>{{ item.reason }}</p></li></ul></article></div>
        <details v-if="e.fields.ai_conclusion" class="legacy-ai"><summary>Подсказка из предыдущей версии анализа</summary><p>{{ e.fields.ai_conclusion }}</p></details>
        <ul v-if="e.fields.warnings?.length" class="ai-questions"><li v-for="warning in e.fields.warnings" :key="warning">{{ warning }}</li></ul>
      </div>
    </section>
    <section class="panel consultation-document final-result" :inert="privacyDirty || undefined">
      <div class="document-heading"><h3>Итоговый результат приёма</h3><span class="hint">Диагноз и назначения врача · PDF и МИС</span></div>
      <section v-for="(group, index) in resultGroups" :key="group.title" class="clinical-section"><h3><span>{{ String(index + 5).padStart(2, '0') }}</span>{{ group.title }}</h3><DiagnosisPicker v-if="group.title === 'Диагноз'" :code="e.fields.diagnosis_code" :disabled="locked" @select="selectDiagnosis"/><div :class="{ 'clinical-two-columns': group.title === 'Анамнез' }"><ClinicalField v-for="key in group.keys" :key="key" v-model="e.fields[key]" :name="key" :label="fields[key]" :disabled="locked" :regeneratable="!readOnly && canGenerate && key !== 'diagnosis'" @regenerate="run(() => generate(key))" :sources="sources(key)" :reviewed="reviewed || e.fields.reviewed_fields?.includes(key)" @update:model-value="changed(key)" @source="showSource" @review="reviewField(key)"/></div><div v-if="group.title === 'Объективные данные'" class="vitals-grid"><label v-for="(label, key) in vitals" :key="key">{{ label }}<input v-model="e.fields[key]" :disabled="locked" placeholder="Не измерено" @input="changed(key)"><button v-if="!readOnly && canGenerate" class="text-button" :disabled="locked" :aria-label="'Перегенерировать: ' + label" @click="run(() => generate(key))">↻ Дополнить с ИИ</button><button v-if="sources(key).length" class="text-button" @click="showSource(sources(key)[0])">Источник ↗</button></label><label>ИМТ, кг/м²<input :value="bodyMassIndex(e.fields)" readonly placeholder="Нужны рост и вес" aria-label="Индекс массы тела"></label></div></section>
      <template v-if="!readOnly"><section class="clinical-section"><h3>Проверка листа</h3><ul class="review-checklist"><li v-for="key in ['complaints', 'anamnesis', 'allergies', 'medications', 'examination', 'follow_up']" :key="key"><span :class="e.fields[key] ? 'consent-ok' : 'consent-no'">{{ e.fields[key] ? 'Есть сведения' : 'Уточнить' }}</span>{{ fields[key] }}</li></ul><label v-if="!reviewed" class="check"><input v-model="approvalChecked" type="checkbox" :disabled="locked">Я проверил данные приёма, диагноз и назначения. Подтверждаю результат приёма.</label></section>
        <p v-if="audioBlob" class="hint">Перед проверкой листа отправьте выбранную запись и дождитесь окончания анализа.</p><div class="document-actions final-actions"><button class="secondary" :disabled="locked || privacyDirty || (reviewed && !dirty)" @click="run(save)"><Save :size="16"/>Сохранить черновик</button><button class="primary" :disabled="locked || reviewed || !approvalChecked || !!audioBlob" @click="run(approve)"><Check :size="17"/>{{ reviewed ? 'Проверено врачом' : 'Проверить' }}</button></div><button v-if="!reviewed" class="text-button reject-draft" :disabled="locked" @click="run(rejectDraft)">Очистить поля и заполнить вручную</button>
        <div v-if="reviewed" class="mis-send"><p><ShieldCheck :size="16"/>Проверенный результат доступен МИС через API</p><button class="secondary full" :disabled="locked || !settings.mis_configured" @click="run(() => queue('send-to-mis', { version: e.version }))"><Send :size="16"/>Отправить в МИС</button><small v-if="!settings.mis_configured">Исходящее подключение МИС не настроено. API чтения доступен.</small><small v-if="e.sent_at">Передано: {{ date(e.sent_at) }}</small></div>
      </template>
    </section>
  </div>
  <div v-if="leaveDestination" class="modal-backdrop" @keydown.esc="!busy && cancelLeave()" @keydown="trapFocus"><section ref="leaveDialog" class="modal leave-modal" role="dialog" aria-modal="true" aria-labelledby="leave-title"><div class="section-title"><h2 id="leave-title">{{ e.ended_at ? 'Сохранить изменения?' : 'Выйти из приёма?' }}</h2><button class="icon-button" aria-label="Продолжить работу" :disabled="busy" @click="cancelLeave"><X/></button></div><p v-if="!e.ended_at">Приостановите приём, чтобы изменить согласие в карте и вернуться. При прерывании новая запись для этого приёма закроется.</p><p>Заполненные поля и запись сохранятся. Пустой лист не попадёт в историю консультаций.</p><p v-if="recording" class="hint">Текущая запись завершится и отправится на распознавание.</p><div v-if="error" class="alert error">{{ error }}</div><div class="leave-actions"><button v-if="!e.ended_at" class="primary" :disabled="busy" @click="run(() => confirmLeave('pause'))"><Pause :size="17"/>Приостановить и вернуться</button><button class="secondary" :disabled="busy" @click="run(() => confirmLeave('interrupt'))">{{ e.ended_at ? 'Сохранить и выйти' : 'Прервать приём' }}</button><button class="text-button" :disabled="busy" @click="cancelLeave">Продолжить работу с листом</button></div></section></div>
</template>
