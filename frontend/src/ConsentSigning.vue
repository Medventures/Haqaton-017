<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { ShieldCheck, FileText, Fingerprint, QrCode, Clock3, X, Download, History, RefreshCw } from 'lucide-vue-next'
import { api } from './api'
import { signData } from './eds'
import SigningMethods from './SigningMethods.vue'
import { mobileSigning } from './signingDevice'

const props = defineProps({ patient: { type: Object, required: true } })
const emit = defineEmits(['updated', 'policy'])
const policy = ref({ signature_required: false, sigex_enabled: false }), current = ref(null), history = ref([]), document = ref(null), attempt = ref(null)
const loading = ref(true), busy = ref(''), error = ref(''), notice = ref(''), openedDocument = ref(''), acknowledged = ref(false), revokeOpen = ref(false), revokeReason = ref('')
const now = ref(Date.now() / 1000), serverOffset = ref(0)
let generation = 0, pollTimer, clockTimer, controller, destroyed = false
const terminal = new Set(['canceled', 'cancelled', 'expired', 'failed', 'rejected'])
const verified = item => item?.state === 'signed' && !!item.verified_at && !item.revoked_at
const signed = computed(() => verified(current.value))
const recorded = computed(() => props.patient.processing_consent ?? !!(props.patient.recording_consent && props.patient.cloud_consent && props.patient.cloud_audio_consent && props.patient.openai_audio_consent))
const pending = computed(() => !!attempt.value && !terminal.has(attempt.value.state) && !verified(attempt.value.consent))
const remaining = computed(() => Math.max(0, Math.ceil((attempt.value?.expires_at || 0) - now.value)))
const clock = computed(() => `${String(Math.floor(remaining.value / 60)).padStart(2, '0')}:${String(remaining.value % 60).padStart(2, '0')}`)
const stateLabel = item => ({ draft: 'Не подписано', signed: item?.verified_at ? 'Подпись проверена' : 'Проверка подписи не завершена', revoked: 'Согласие отозвано', canceled: 'Подписание отменено', cancelled: 'Подписание отменено', expired: 'Срок подписания истёк', failed: 'Подпись не подтверждена', rejected: 'Подпись не подтверждена' }[item?.state] || 'Ожидает подписи')
const method = value => ({ eds: 'ЭЦП / NCALayer', qr: 'eGov Mobile / SIGEX' }[value] || 'Электронная подпись')
const date = value => value ? new Date(value * 1000).toLocaleString('ru-RU') : '—'
const pdfUrl = item => `/api/v1/patient-consents/${encodeURIComponent(item.id)}/pdf`
const signatureUrl = item => `/api/v1/patient-consents/${encodeURIComponent(item.id)}/signature`
const attemptPath = () => `/patient-consents/${document.value.id}/attempts/${attempt.value.id}`
const stillCurrent = token => !destroyed && token === generation

function stopPolling() { clearTimeout(pollTimer); pollTimer = null }
function syncClock(value) { if (value?.server_time) { serverOffset.value = value.server_time - Date.now() / 1000; now.value = value.server_time } }
function chooseDocument(value) {
  if (document.value?.id !== value?.id) { openedDocument.value = ''; acknowledged.value = false }
  document.value = value
}
async function load(token = generation, resume = false) {
  const result = await api(`/patients/${props.patient.id}/consents`)
  if (!stillCurrent(token)) return
  policy.value = result.policy; current.value = result.current; history.value = result.history || []
  emit('policy', result.policy)
  if (!document.value) chooseDocument(signed.value ? current.value : history.value.find(item => ['draft', 'pending'].includes(item.state)) || (['draft', 'pending'].includes(current.value?.state) ? current.value : null))
  else if (document.value.id === current.value?.id) chooseDocument(current.value)
  if (resume && document.value?.active_attempt) {
    attempt.value = document.value.active_attempt
    if (attempt.value.id) await poll(token)
  }
}
async function applyAttempt(value, token) {
  if (!stillCurrent(token)) return
  syncClock(value)
  attempt.value = { ...attempt.value, ...value }
  if (value.consent) chooseDocument(value.consent)
  if (verified(value.consent)) {
    stopPolling(); current.value = value.consent; notice.value = 'Подпись пациента проверена. Согласие оформлено.'
    emit('updated', value.patient || null)
    await load(token)
  } else if (terminal.has(value.state)) {
    stopPolling()
    if (value.state === 'expired') notice.value = 'Время подписания истекло. Можно создать новую попытку.'
    else if (['canceled', 'cancelled'].includes(value.state)) notice.value = 'Попытка подписания отменена.'
    else error.value = value.error || 'Подпись не подтверждена. Проверьте, что документ подписывает пациент из этой карты.'
  }
}
async function poll(token = generation) {
  stopPolling()
  if (!stillCurrent(token) || !attempt.value?.id || !document.value?.id) return
  try {
    const result = await api(attemptPath())
    if (!stillCurrent(token)) return
    error.value = ''; await applyAttempt(result, token)
    if (stillCurrent(token) && pending.value) {
      pollTimer = setTimeout(() => poll(token), 3000)
    }
  } catch (err) {
    if (!stillCurrent(token)) return
    error.value = err.message
    if (pending.value && ![401, 403, 404].includes(err.status)) pollTimer = setTimeout(() => poll(token), 5000)
  }
}
async function prepare() {
  const token = ++generation; stopPolling(); error.value = ''; notice.value = ''; busy.value = 'prepare'; attempt.value = null
  try {
    const value = await api(`/patients/${props.patient.id}/consents`, { method: 'POST', body: {} })
    if (!stillCurrent(token)) return
    chooseDocument(value); await load(token)
  } catch (err) { if (stillCurrent(token)) error.value = err.message }
  finally { if (stillCurrent(token)) busy.value = '' }
}
async function start(selectedMethod) {
  selectedMethod = selectedMethod === 'mobile' ? 'qr' : selectedMethod
  if (!acknowledged.value || openedDocument.value !== document.value?.id) return
  const consentId = document.value.id, token = ++generation
  let signatureSubmitted = false
  stopPolling(); error.value = ''; notice.value = ''; busy.value = 'start'
  controller?.abort(); controller = new AbortController()
  try {
    const value = await api(`/patient-consents/${consentId}/start`, { method: 'POST', body: { method: selectedMethod, acknowledged: true } })
    if (!stillCurrent(token)) {
      // Ответ пришёл после ухода из карты: недоступную пользователю попытку закрываем.
      if (value.id) await api(`/patient-consents/${consentId}/attempts/${value.id}/cancel`, { method: 'POST', body: {} }).catch(() => {})
      return
    }
    attempt.value = { ...value, method: selectedMethod }; syncClock(value)
    if (selectedMethod === 'eds') {
      if (!value.data_base64) throw new Error('Сервер не передал документ для подписания')
      const signature = await signData(value.data_base64, { signal: controller.signal })
      if (!stillCurrent(token)) return
      attempt.value = { ...attempt.value, state: 'verifying' }
      signatureSubmitted = true
      await applyAttempt(await api(attemptPath() + '/signature', { method: 'POST', body: { signature } }), token)
      if (stillCurrent(token) && pending.value) pollTimer = setTimeout(() => poll(token), 1500)
    } else pollTimer = setTimeout(() => poll(token), 2500)
  } catch (err) {
    if (stillCurrent(token)) {
      error.value = err.message
      if (selectedMethod === 'eds' && !signatureSubmitted && attempt.value?.id && pending.value) {
        try { await applyAttempt(await api(attemptPath() + '/cancel', { method: 'POST', body: {} }), token) }
        catch { if (stillCurrent(token)) pollTimer = setTimeout(() => poll(token), 3000) }
      } else if (attempt.value?.id && pending.value) pollTimer = setTimeout(() => poll(token), 3000)
    }
  }
  finally { if (stillCurrent(token)) busy.value = '' }
}
async function cancel() {
  const path = attemptPath(), token = ++generation
  stopPolling(); controller?.abort(); error.value = ''; notice.value = ''; busy.value = 'cancel'
  try { await applyAttempt(await api(path + '/cancel', { method: 'POST', body: {} }), token); await load(token) }
  catch (err) { if (stillCurrent(token)) { error.value = err.message; await poll(token) } }
  finally { if (stillCurrent(token)) busy.value = '' }
}
async function setManual(event) {
  const token = generation; error.value = ''; notice.value = ''; busy.value = 'manual'
  try {
    const value = await api(`/patients/${props.patient.id}/consent`, { method: 'PATCH', body: { processing_consent: event.target.checked } })
    if (!stillCurrent(token)) return
    emit('updated', value); await load(token); notice.value = value.processing_consent ? 'Решение пациента зафиксировано врачом. Электронная подпись ещё не оформлена.' : 'Обработка новых записей отключена.'
  } catch (err) { if (stillCurrent(token)) error.value = err.message }
  finally { if (stillCurrent(token)) { busy.value = ''; event.target.checked = recorded.value } }
}
async function revoke() {
  const token = ++generation; stopPolling(); controller?.abort(); busy.value = 'revoke'; error.value = ''; notice.value = ''
  try {
    const value = await api(`/patient-consents/${current.value.id}/revoke`, { method: 'POST', body: { reason: revokeReason.value.trim() } })
    if (!stillCurrent(token)) return
    if (value.consent) current.value = value.consent
    chooseDocument(null); attempt.value = null; revokeOpen.value = false; revokeReason.value = ''
    emit('updated', value.patient || null); await load(token); notice.value = 'Согласие отозвано. Архив документов и подписей сохранён.'
  } catch (err) { if (stillCurrent(token)) error.value = err.message }
  finally { if (stillCurrent(token)) busy.value = '' }
}
function closeUnsubmittedAttempt() {
  if (attempt.value?.id && attempt.value.method === 'eds' && attempt.value.state === 'pending' && document.value?.id) {
    api(attemptPath() + '/cancel', { method: 'POST', body: {} }).catch(() => {})
  }
}
watch(() => props.patient.id, async () => {
  closeUnsubmittedAttempt()
  const token = ++generation; controller?.abort(); stopPolling(); clearInterval(clockTimer)
  loading.value = true; current.value = null; history.value = []; chooseDocument(null); attempt.value = null; error.value = ''; notice.value = ''; busy.value = ''; revokeOpen.value = false
  clockTimer = setInterval(() => { now.value = Date.now() / 1000 + serverOffset.value }, 500)
  try { await load(token, true) } catch (err) { if (stillCurrent(token)) error.value = err.message }
  finally { if (stillCurrent(token)) loading.value = false }
}, { immediate: true })
function resumeSigning() { if (globalThis.document.visibilityState === 'visible' && pending.value) poll() }
onMounted(() => { window.addEventListener('focus', resumeSigning); globalThis.document.addEventListener('visibilitychange', resumeSigning) })
onBeforeUnmount(() => { window.removeEventListener('focus', resumeSigning); globalThis.document.removeEventListener('visibilitychange', resumeSigning); closeUnsubmittedAttempt(); destroyed = true; generation++; controller?.abort(); stopPolling(); clearInterval(clockTimer) })
</script>

<template>
  <section class="panel consent-signing" aria-labelledby="consent-signing-title">
    <div class="consent-signing-heading"><ShieldCheck :size="25"/><div><h3 id="consent-signing-title">Согласие пациента</h3><p>На запись консультации и обработку данных с помощью ИИ</p></div><span v-if="signed" class="badge approved">Подписано ЭЦП пациента</span><span v-else-if="recorded" class="badge consent-manual">Зафиксировано врачом · без ЭЦП</span><span v-else class="badge neutral">Не оформлено</span></div>
    <p v-if="loading" class="hint" role="status">Проверяем документы согласия…</p>
    <div v-if="error" class="alert error" role="alert">{{ error }}</div><div v-if="notice" class="alert success" role="status">{{ notice }}</div>
    <template v-if="!loading">
      <div v-if="signed" class="signed-consent"><dl><div><dt>Подпись проверена</dt><dd>{{ date(current.verified_at) }}</dd></div><div><dt>Способ подписания</dt><dd>{{ method(current.method) }}</dd></div><div><dt>ИИН подписанта</dt><dd>{{ current.signer_iin_masked || 'Подтверждён сервером' }}</dd></div></dl><div class="inline-actions"><a class="secondary consent-file-link" :href="pdfUrl(current)" target="_blank" rel="noopener"><FileText :size="17"/>Подписанный документ</a><a class="text-button" :href="signatureUrl(current)" download><Download :size="16"/>Скачать подпись</a><button class="text-button" :disabled="!!busy" @click="revokeOpen = !revokeOpen">Отозвать согласие</button></div><div v-if="revokeOpen" class="consent-revoke"><p>Отзыв запретит обработку новых записей. Сохранённые записи и подписанные документы останутся в архиве.</p><label>Причина отзыва <span class="hint">необязательно</span><textarea v-model="revokeReason" maxlength="500" rows="2" :disabled="!!busy"/></label><div class="inline-actions"><button class="danger" :disabled="!!busy" @click="revoke">Подтвердить отзыв</button><button class="secondary" :disabled="!!busy" @click="revokeOpen = false">Оставить согласие</button></div></div></div>
      <template v-else>
        <p class="consent-data-notice">Согласие включает передачу исходной аудиозаписи в OpenAI, в том числе возможных персональных и медицинских данных. Маскирование исходного аудио выполняется после распознавания.</p>
        <p v-if="policy.signature_required" class="alert warning">Запись и обработка с ИИ станут доступны после проверки электронной подписи пациента. До этого лист можно заполнять вручную.</p>
        <div v-else class="consent-manual-box"><label class="check"><input type="checkbox" :checked="recorded" :disabled="!!busy || pending" @change="setManual">Пациент согласен на запись и обработку данных, включая передачу аудио в OpenAI; решение зафиксировано врачом</label><p class="hint">Эта отметка не заменяет электронную подпись пациента. Оформить ЭЦП можно ниже.</p></div>
        <div v-if="!document" class="consent-prepare"><button class="secondary" :disabled="!!busy" @click="prepare"><FileText :size="17"/>{{ busy === 'prepare' ? 'Подготавливаем документ…' : 'Подготовить согласие для подписи' }}</button></div>
        <div v-else class="consent-document"><div class="consent-document-title"><FileText :size="20"/><div><strong>Согласие на запись и обработку данных</strong><span class="hint">Версия {{ document.version }} · {{ date(document.created_at) }}</span></div></div><a class="secondary consent-file-link" :href="pdfUrl(document)" target="_blank" rel="noopener" @click="openedDocument = document.id"><FileText :size="17"/>Открыть точный документ PDF</a><details class="consent-document-hash"><summary>Контрольная сумма документа</summary><code>{{ document.document_sha256 || document.sha256 }}</code></details>
          <template v-if="!pending"><label class="check consent-acknowledge"><input v-model="acknowledged" type="checkbox" :disabled="openedDocument !== document.id || !!busy">Пациент ознакомился с открытым документом; его можно передать на подпись</label><p v-if="openedDocument !== document.id" class="hint">Сначала откройте PDF и предоставьте документ пациенту.</p><SigningMethods :disabled="!!busy || !acknowledged || !policy.sigex_enabled" @select="start"/></template>
          <div v-else class="consent-waiting" role="status"><div class="consent-waiting-heading"><Clock3 :size="20"/><strong>{{ attempt.state === 'verifying' || attempt.state === 'signed' ? 'Подпись получена — проверяем сертификат' : 'Ожидаем подпись пациента' }}</strong><span v-if="attempt.expires_at" class="consent-expiry">{{ clock }}</span></div><p v-if="attempt.state === 'verifying'">Повторно подписывать не нужно. Статус обновится после проверки SIGEX.</p><template v-else-if="attempt.method === 'qr'"><img v-if="!mobileSigning && attempt.qr_image" class="consent-qr" :src="attempt.qr_image" alt="QR согласия для подписания пациентом в eGov Mobile"><p>{{ mobileSigning ? 'Откройте eGov Mobile по ссылке ниже и подпишите согласие пациента.' : 'Пациент должен открыть eGov Mobile и отсканировать QR.' }} После подписания сервер проверит документ и совпадение ИИН.</p><a v-if="mobileSigning && attempt.launch_url" :href="attempt.launch_url" class="text-button">Открыть eGov Mobile на этом устройстве</a></template><p v-else>Выберите ЭЦП пациента в NCALayer. ИИН владельца подписи должен совпадать с ИИН в этой карте.</p><div class="inline-actions"><button class="secondary" :disabled="busy === 'cancel'" @click="cancel"><X :size="16"/>Отменить подписание</button><button class="text-button" :disabled="!!busy" @click="poll()"><RefreshCw :size="16"/>Проверить статус</button></div></div>
        </div>
        <p v-if="!policy.sigex_enabled" class="hint">Подписание через SIGEX пока не подключено. Обратитесь к администратору.</p>
      </template>
      <details v-if="history.length" class="consent-history"><summary><History :size="17"/>История документов согласия <span class="count">{{ history.length }}</span></summary><article v-for="item in history" :key="item.id"><div><strong>{{ stateLabel(item) }}</strong><span class="hint">{{ date(item.verified_at || item.created_at) }} · версия {{ item.version }}</span><span v-if="item.revoked_at" class="hint">Отозвано: {{ date(item.revoked_at) }}</span></div><div class="inline-actions"><a class="text-button" :href="pdfUrl(item)" target="_blank" rel="noopener">PDF</a><a v-if="item.verified_at" class="text-button" :href="signatureUrl(item)" download>Подпись</a></div></article></details>
    </template>
  </section>
</template>

<style scoped>
.consent-signing{padding:24px 26px}.consent-signing-heading{display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap;margin-bottom:19px;color:#8A5C43}.consent-signing-heading>svg{flex-shrink:0;margin-top:4px}.consent-signing-heading>div{flex:1;min-width:210px}.consent-signing-heading h3{font-size:23px;margin-bottom:5px}.consent-signing-heading p{font-size:14px;color:#887565;margin:0}.consent-manual{background:#FFF7E7;color:#93672B}.consent-data-notice{font-size:14px;line-height:1.75;margin:0 0 17px;color:#6F5947}.consent-manual-box{padding:16px 18px;background:#F9F5F1;border-radius:16px;margin-bottom:20px}.consent-manual-box .check{font-size:14px;line-height:1.7}.consent-manual-box .hint{margin:9px 0 0}.consent-prepare{margin:15px 0}.consent-document{border:1px solid #E9CFBA;border-radius:18px;padding:20px}.consent-document-title{display:flex;gap:11px;align-items:flex-start;margin-bottom:16px;color:#8A5C43}.consent-document-title>div{display:flex;flex-direction:column;gap:5px}.consent-document-title strong{font-size:16px;font-weight:600}.consent-file-link{display:inline-flex;align-items:center;justify-content:center;gap:9px;border:1px solid #E9CFBA;border-radius:14px;padding:11px 15px;font-size:14px;text-decoration:none;line-height:1.5}.consent-file-link:hover{background:#F5EBDD}.consent-document-hash{margin:14px 0;color:#887565;font-size:12px}.consent-document-hash summary{cursor:pointer}.consent-document-hash code{display:block;margin-top:7px;word-break:break-all;font-size:12px;color:#887565}.consent-acknowledge{font-size:14px;margin:18px 0 10px;line-height:1.7}.consent-signing-methods{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}.consent-signing-methods button{justify-content:flex-start;flex:1;min-width:190px;text-align:left;font-size:14px;line-height:1.5}.consent-signing-methods small{display:block;font-size:11px;font-weight:400;margin-top:3px;opacity:.9}.consent-waiting{background:#FFFCF9;border-top:1px solid #E9CFBA;margin-top:18px;padding-top:18px}.consent-waiting-heading{display:flex;align-items:center;gap:9px;flex-wrap:wrap;color:#8A5C43;font-size:15px}.consent-expiry{font-variant-numeric:tabular-nums;margin-left:auto;background:#F5EBDD;border-radius:9px;padding:4px 9px}.consent-qr{display:block;width:210px;height:210px;object-fit:contain;background:#fff;border:1px solid #E9CFBA;border-radius:14px;padding:10px;margin:21px auto 15px}.consent-waiting p{font-size:14px;color:#887565;line-height:1.7;margin:14px 0}.consent-waiting>.inline-actions{margin-top:15px;flex-wrap:wrap}.signed-consent dl{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:0 0 21px}.signed-consent dt{font-size:12px;color:#887565;margin-bottom:6px}.signed-consent dd{margin:0;color:#3E2E20;font-size:14px;line-height:1.6;overflow-wrap:anywhere}.signed-consent>.inline-actions{flex-wrap:wrap}.consent-revoke{border:1px solid #E9CFBA;background:#FFF9F3;border-radius:16px;padding:17px;margin-top:20px}.consent-revoke p{font-size:14px;line-height:1.7}.consent-revoke label{font-size:14px}.consent-revoke>.inline-actions{margin-top:15px;flex-wrap:wrap}.consent-history{margin-top:24px;border-top:1px solid #E9CFBA;padding-top:18px}.consent-history>summary{display:flex;align-items:center;gap:9px;cursor:pointer;color:#8A5C43;font-size:14px;line-height:1.5}.consent-history article{display:flex;justify-content:space-between;align-items:center;gap:15px;border-top:1px solid #F0E0D1;padding:15px 0;margin-top:13px}.consent-history article>div:first-child{display:flex;flex-direction:column;gap:5px}.consent-history article strong{font-size:14px;font-weight:550}.consent-history article .hint{font-size:12px}.consent-signing>.alert{font-size:14px}.consent-signing>.hint{margin-bottom:0}@media(max-width:700px){.consent-signing{padding:22px 20px}.signed-consent dl{grid-template-columns:1fr 1fr}.signed-consent dl>div:last-child{grid-column:1/-1}.consent-signing-heading h3{font-size:22px}.consent-signing-heading>svg{display:none}.consent-document{padding:17px}.consent-signing-methods{flex-direction:column}.consent-history>summary{flex-wrap:wrap}.consent-history article{align-items:flex-start}.consent-waiting-heading strong{flex:1;min-width:170px}.consent-expiry{margin-left:29px}}@media(max-width:420px){.signed-consent dl{grid-template-columns:1fr}.signed-consent dl>div:last-child{grid-column:auto}.consent-file-link{width:100%}.consent-waiting .secondary{font-size:13px}.consent-history article{flex-wrap:wrap}}
</style>
