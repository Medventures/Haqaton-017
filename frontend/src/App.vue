<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { Users, Search, Plus, ChevronRight, ArrowLeft, Settings2, ShieldCheck, LogOut, CalendarDays, ArrowUpRight, X, FileText, Play, Stethoscope } from 'lucide-vue-next'
import Auth from './Auth.vue'
import MaskedInput from './MaskedInput.vue'
import { birthDateFromIin, formatPhone, formatIin } from './patient-input'
import Consultation from './Consultation.vue'
import Settings from './Settings.vue'
import UmcLogo from './UmcLogo.vue'
import ConsentSigning from './ConsentSigning.vue'
import { api } from './api'

const doctor = ref(null), loading = ref(true), page = ref('patients')
const patients = ref([]), search = ref(''), searched = ref(false), lastSearch = ref(''), morePatients = ref(false), offset = ref(0)
const patient = ref(null), encounters = ref([]), encounter = ref(null), pausedEncounter = ref(null), consultation = ref(null)
const ownEncounters = ref([]), ownFilter = ref('all'), ownOffset = ref(0), moreOwn = ref(false)
const error = ref(''), busy = ref(false), createOpen = ref(false), settings = ref({}), consentPolicy = ref(null)
const visitType = ref('primary'), previousEncounter = ref('')
const emptyPatient = () => ({ name: '', iin: '', birth_date: '', phone: '', sex: 'unknown', processing_consent: false, external_id: null })
const form = ref(emptyPatient())
watch(() => form.value.iin, (value, previous) => {
  const inferred = birthDateFromIin(value)
  if (inferred) form.value.birth_date = inferred
  else if (form.value.birth_date === birthDateFromIin(previous)) form.value.birth_date = ''
})
const initials = name => (name || '').split(' ').filter(Boolean).slice(0, 2).map(x => x[0]).join('')
const date = value => value ? new Date(value * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'
const birthDate = value => value ? new Date(`${value}T12:00:00`).toLocaleDateString('ru-RU') : 'Не указана'
const statuses = { draft: 'Черновик', ready: 'Готов к проверке', processing: 'Обработка', approved: 'Проверено врачом', exported: 'Передан в МИС' }
const title = computed(() => encounter.value ? 'Консультация' : page.value === 'settings' ? 'Настройки' : page.value === 'encounters' ? 'Мои приёмы' : patient.value ? 'Карта пациента' : 'Поиск пациентов')
const priorOptions = computed(() => encounters.value.filter(e => e.status !== 'processing'))
const shownOwn = computed(() => ownEncounters.value)
const consent = p => p?.ai_processing_allowed ?? p?.processing_consent ?? !!(p?.recording_consent && p?.cloud_consent && p?.cloud_audio_consent && p?.openai_audio_consent)
const encounterStatus = e => e.status === 'processing' && e.reviewed_at ? 'Отправка в МИС' : statuses[e.status]
const encounterTitle = e => (e.visit_type || e.fields?.visit_type) === 'repeat' ? 'Повторная консультация' : 'Первичная консультация'
const canResume = computed(() => pausedEncounter.value && pausedEncounter.value.patient_id === patient.value?.id && !pausedEncounter.value.ended_at)
const signatureRequired = computed(() => consentPolicy.value?.signature_required ?? settings.value.consent_signature_required ?? true)
watch(search, value => { if (!value.trim()) { patients.value = []; searched.value = false; morePatients.value = false } })

async function run(fn) { error.value = ''; busy.value = true; try { await fn() } catch (e) { error.value = e.message; if (e.status === 401) doctor.value = null } finally { busy.value = false } }
async function loadPatients(reset = true) {
  const query = reset ? search.value.trim() : lastSearch.value
  if (!query) { patients.value = []; searched.value = false; morePatients.value = false; return }
  const targetOffset = reset ? 0 : offset.value + 30
  const list = await api(`/patients?q=${encodeURIComponent(query)}&offset=${targetOffset}&limit=30`)
  offset.value = targetOffset; lastSearch.value = query; searched.value = true; morePatients.value = list.length === 30
  patients.value = reset ? list : [...patients.value, ...list]
  if (reset) { patient.value = null; encounter.value = null; page.value = 'patients' }
}
async function login(value) { doctor.value = value; await run(async () => { settings.value = await api('/settings') }) }
async function patientDetails(id) {
  const [p, list] = await Promise.all([api(`/patients/${id}`), api(`/patients/${id}/encounters`)])
  patient.value = p; encounters.value = list
  patients.value = patients.value.map(item => item.id === p.id ? p : item)
  visitType.value = list.some(e => ['approved', 'exported'].includes(e.status)) ? 'repeat' : 'primary'
  previousEncounter.value = list.find(e => ['approved', 'exported'].includes(e.status))?.id || ''
}
async function openPatient(p) { await run(async () => { await patientDetails(p.id); encounter.value = null; page.value = 'patients' }) }
async function newPatient() {
  await run(async () => {
    const p = await api('/patients', { method: 'POST', body: { ...form.value, processing_consent: signatureRequired.value ? false : form.value.processing_consent } })
    createOpen.value = false; form.value = emptyPatient(); patient.value = p; encounters.value = []; encounter.value = null; page.value = 'patients'; visitType.value = 'primary'; previousEncounter.value = ''
  })
}
async function startEncounter() {
  await run(async () => {
    encounter.value = await api(`/patients/${patient.value.id}/encounters`, { method: 'POST', body: { visit_type: visitType.value, previous_encounter_id: visitType.value === 'repeat' ? previousEncounter.value || null : null } })
    pausedEncounter.value = null
  })
}
async function openEncounter(value) {
  const id = typeof value === 'string' ? value : value.id
  await run(async () => { const item = await api(`/encounters/${id}`); if (patient.value?.id !== item.patient_id) await patientDetails(item.patient_id); encounter.value = item })
}
async function resumeEncounter() {
  await run(async () => {
    const saved = pausedEncounter.value
    encounter.value = await api(`/encounters/${saved.id}/resume`, { method: 'POST', body: { version: saved.version, draft_token: saved.draft_token } })
    pausedEncounter.value = null
  })
}
function encounterUpdated(value) {
  encounter.value = value
  if (value.paused_at && !value.ended_at) pausedEncounter.value = value
  else if (pausedEncounter.value?.id === value.id) pausedEncounter.value = null
}
async function back(result) {
  if (result?.encounter) {
    const item = result.encounter
    if (result.action === 'pause' && !result.discarded && !item.ended_at) pausedEncounter.value = item
    else if (pausedEncounter.value?.id === item.id) pausedEncounter.value = null
  }
  encounter.value = null
  const destination = result?.destination || 'patient'
  if (destination === 'logout') { await logout(); return }
  if (destination !== 'patient') { await go(destination === 'visits' ? 'encounters' : destination); return }
  page.value = 'patients'
  if (patient.value) await run(() => patientDetails(patient.value.id))
}
async function loadOwn(reset = true) {
  const targetOffset = reset ? 0 : ownOffset.value + 30
  const status = ownFilter.value === 'drafts' ? 'unreviewed' : ownFilter.value === 'approved' ? 'reviewed' : ''
  const list = await api(`/encounters?offset=${targetOffset}&limit=30&status=${status}`)
  ownOffset.value = targetOffset; moreOwn.value = list.length === 30; ownEncounters.value = reset ? list : [...ownEncounters.value, ...list]
}
async function filterOwn(value) { ownFilter.value = value; ownEncounters.value = []; ownOffset.value = 0; moreOwn.value = false; await run(() => loadOwn()) }
async function go(pageName) {
  if (encounter.value) { consultation.value?.requestLeave?.(pageName); return }
  page.value = pageName; patient.value = null
  if (pageName === 'encounters') await run(() => loadOwn())
}
async function logout() {
  if (encounter.value) { consultation.value?.requestLeave?.('logout'); return }
  await run(async () => { await api('/auth/logout', { method: 'POST' }); doctor.value = null; patient.value = null; encounter.value = null; pausedEncounter.value = null; patients.value = []; ownEncounters.value = []; search.value = ''; searched.value = false })
}
async function consentUpdated(value) {
  const patientId = patient.value?.id
  if (!patientId) return
  // Ответ операции согласия может быть кратким; берём актуальную политику доступа из карточки.
  const refreshed = await api(`/patients/${patientId}`)
  if (patient.value?.id !== patientId) return
  patient.value = refreshed
  patients.value = patients.value.map(item => item.id === patientId ? patient.value : item)
}
function showCreate() { error.value = ''; createOpen.value = true }
onMounted(async () => { try { await login(await api('/auth/me')) } catch {} finally { loading.value = false } })
</script>

<template>
  <div v-if="loading" class="initial-loading">Smart Consult<span>Загружаем кабинет…</span></div>
  <Auth v-else-if="!doctor" @login="login"/>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <a class="platform-brand" href="/" @click.prevent="go('patients')"><UmcLogo :caption="false"/><strong>Smart Consult</strong><span>Консультация с AI / ИИ-ассистентом</span></a>
      <nav aria-label="Кабинет врача"><button :class="{ active: page === 'patients' }" @click="go('patients')"><Users :size="20"/>Пациенты<ChevronRight class="nav-arrow" :size="16"/></button><button :class="{ active: page === 'encounters' }" @click="go('encounters')"><FileText :size="20"/>Мои приёмы</button><button :class="{ active: page === 'settings' }" @click="go('settings')"><Settings2 :size="20"/>Настройки</button></nav>
      <div class="sidebar-bottom"><ShieldCheck :size="17"/><span>Под контролем врача</span></div>
      <div class="doctor-card"><Stethoscope :size="23"/><div><span>Врач</span><strong>{{ doctor.name }}</strong></div><button class="icon-button" aria-label="Выйти из кабинета" @click="logout"><LogOut :size="18"/></button></div>
      <div class="hackathon-credit">Создано в рамках хакатона medhub</div>
    </aside>
    <div class="main-shell"><header class="topbar"><div class="breadcrumb">Кабинет врача <ChevronRight :size="15"/><strong>{{ title }}</strong></div></header><main>
      <div v-if="error" class="alert error" role="alert">{{ error }}<button class="icon-button" aria-label="Закрыть сообщение" @click="error = ''"><X :size="17"/></button></div>
      <Consultation v-if="encounter" :key="encounter.id" ref="consultation" :initial="encounter" :patient="patient" :settings="settings" :doctor="doctor" @back="back" @updated="encounterUpdated" @open-previous="openEncounter"/>
      <Settings v-else-if="page === 'settings'" :settings="settings"/>
      <template v-else-if="page === 'encounters'">
        <div class="page-heading compact-heading"><div><span class="eyebrow">ЛИЧНЫЙ КАБИНЕТ</span><h1>Мои приёмы</h1><p class="muted">Консультации ваших пациентов и листы, ожидающие проверки.</p></div></div>
        <section class="panel"><div class="list-toolbar"><div class="filter-tabs" aria-label="Фильтр приёмов"><button :class="{ active: ownFilter === 'all' }" :disabled="busy" @click="filterOwn('all')">Все</button><button :class="{ active: ownFilter === 'drafts' }" :disabled="busy" @click="filterOwn('drafts')">Нужна проверка</button><button :class="{ active: ownFilter === 'approved' }" :disabled="busy" @click="filterOwn('approved')">Проверенные</button></div><button class="text-button" :disabled="busy" @click="run(() => loadOwn())">Обновить</button></div>
          <button v-for="e in shownOwn" :key="e.id" class="encounter-row" @click="openEncounter(e)"><div class="document-icon"><FileText :size="23"/></div><div><strong>{{ e.patient?.name || e.patient_name || 'Пациент' }}</strong><span>{{ encounterTitle(e) }} · {{ date(e.started_at || e.created_at) }}</span><span v-if="e.ended_at">Приём завершён<template v-if="!e.reviewed_at"> · лист можно доработать</template></span><span v-else>Приём не завершён</span></div><span class="badge" :class="e.status">{{ encounterStatus(e) }}</span><ChevronRight :size="18"/></button>
          <div v-if="!shownOwn.length" class="empty compact"><FileText :size="36"/><h3>{{ ownFilter === 'drafts' ? 'Нет листов для проверки' : 'Приёмов пока нет' }}</h3><p>Здесь сохраняются консультации с записями или заполненными полями.</p></div><div v-if="moreOwn" class="table-footer"><button class="text-button" :disabled="busy" @click="run(() => loadOwn(false))">Показать ещё</button></div>
        </section>
      </template>
      <template v-else>
        <section class="panel search-panel"><form class="search-row" @submit.prevent="!busy && run(() => loadPatients())"><label class="search-field"><Search :size="21"/><input v-model="search" placeholder="ФИО или полный ИИН пациента" aria-label="Поиск пациента" autocomplete="off"><button v-if="search" type="button" class="icon-button" aria-label="Очистить поиск" @click="search = ''"><X :size="17"/></button></label><button class="primary" :disabled="busy || !search.trim()">Найти пациента</button><button class="secondary" type="button" @click="showCreate"><Plus :size="18"/>Добавить пациента</button></form></section>
        <template v-if="patient">
          <button class="text-button back" @click="patient = null"><ArrowLeft :size="17"/>К результатам поиска</button>
          <section class="panel patient-card"><div class="patient-card-heading"><div><span class="eyebrow">КАРТА ПАЦИЕНТА</span><h1>{{ patient.name }}</h1><p class="muted">ИИН {{ formatIin(patient.iin) }}</p></div><span class="avatar patient-avatar">{{ initials(patient.name) }}</span></div><div class="patient-summary"><div><span>Дата рождения</span><strong>{{ birthDate(patient.birth_date) }}</strong></div><div><span>Телефон</span><strong>{{ formatPhone(patient.phone) || 'Не указан' }}</strong></div><div><span>Пол</span><strong>{{ { female: 'Женский', male: 'Мужской', unknown: 'Не указан' }[patient.sex] }}</strong></div><div><span>ID в МИС</span><strong>{{ patient.external_id || 'Не указан' }}</strong></div></div></section>
          <ConsentSigning :key="patient.id" :patient="patient" @updated="value => run(() => consentUpdated(value))" @policy="consentPolicy = $event"/>
          <section class="panel begin-encounter"><div><h3>{{ canResume ? 'Приём приостановлен' : 'Новая консультация' }}</h3><p class="hint">{{ canResume ? 'Вернитесь к листу консультации после изменения данных или согласия пациента.' : 'Первичный приём или продолжение наблюдения с предыдущим заключением.' }}</p></div><template v-if="!canResume"><label>Тип приёма<select v-model="visitType"><option value="primary">Первичный</option><option value="repeat">Повторный</option></select></label><label v-if="visitType === 'repeat'">Предыдущий приём<select v-model="previousEncounter"><option value="">Без привязки</option><option v-for="item in priorOptions" :key="item.id" :value="item.id">{{ date(item.started_at || item.created_at) }} · {{ item.physician_name || item.doctor_name || 'Консультация' }}</option></select></label><button class="primary" :disabled="busy" @click="startEncounter"><Play :size="18"/>Начать приём</button></template><button v-else class="primary" :disabled="busy" @click="resumeEncounter"><Play :size="18"/>Продолжить приём</button></section>
          <section class="panel"><div class="section-title"><h3>История консультаций <span class="count">{{ encounters.length }}</span></h3><CalendarDays :size="23"/></div><div v-if="!encounters.length" class="empty compact"><CalendarDays :size="36"/><h3>Первый приём ещё впереди</h3><p>Заполненные листы появятся здесь после начала консультации.</p></div><button v-for="e in encounters" :key="e.id" class="encounter-row" @click="openEncounter(e)"><div class="document-icon"><FileText :size="23"/></div><div><strong>{{ encounterTitle(e) }}</strong><span>{{ date(e.started_at || e.created_at) }}<template v-if="e.physician_name || e.doctor_name"> · {{ e.physician_name || e.doctor_name }}</template></span><span v-if="e.previous_encounter_id">Связан с предыдущим приёмом</span><span v-if="e.read_only">Заключение другого врача · просмотр</span></div><span class="badge" :class="e.status">{{ encounterStatus(e) }}</span><ChevronRight :size="18"/></button></section>
        </template>
        <template v-else>
          <div class="page-heading compact-heading"><div><h1>Пациенты</h1><p class="muted">Найдите пациента по ФИО или ИИН, чтобы открыть карту и начать приём.</p></div></div>
          <section class="panel patient-list"><div class="section-title"><h3>{{ searched ? 'Результаты поиска' : 'Поиск по общей базе пациентов' }} <span v-if="searched" class="count">{{ patients.length }}</span></h3><span v-if="searched" class="hint">«{{ lastSearch }}»</span></div>
            <div class="table-wrap"><table v-if="patients.length"><thead><tr><th>ПАЦИЕНТ</th><th>ИИН</th><th>ДАТА РОЖДЕНИЯ</th><th>ОБРАБОТКА С ИИ</th><th></th></tr></thead><tbody><tr v-for="p in patients" :key="p.id"><td><button class="patient-name" @click="openPatient(p)"><span class="avatar">{{ initials(p.name) }}</span><strong>{{ p.name }}</strong></button></td><td class="mono">{{ formatIin(p.iin) }}</td><td>{{ birthDate(p.birth_date) }}</td><td><span class="badge" :class="consent(p) ? 'approved' : 'neutral'">{{ consent(p) ? 'Обработка разрешена' : 'Без записи' }}</span></td><td><button class="icon-button" :aria-label="'Открыть карту: ' + p.name" @click="openPatient(p)"><ArrowUpRight :size="21"/></button></td></tr></tbody></table></div>
            <div v-if="!patients.length" class="empty"><Search :size="38"/><h3>{{ searched ? 'Пациент не найден' : 'Начните с поиска' }}</h3><p>{{ searched ? 'Проверьте ФИО или ИИН. Для нового пациента создайте карточку.' : 'Карточки отображаются только после запроса. Поиск доступен каждому врачу.' }}</p><button v-if="searched" class="secondary" @click="showCreate"><Plus :size="17"/>Создать карточку</button></div><div v-if="morePatients" class="table-footer"><button class="text-button" :disabled="busy" @click="run(() => loadPatients(false))">Показать ещё</button></div>
          </section>
        </template>
      </template>
    </main></div>
    <div v-if="createOpen" class="modal-backdrop" @click.self="!busy && (createOpen = false)" @keydown.esc="!busy && (createOpen = false)"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="patient-modal-title"><div class="section-title"><div><span class="eyebrow">НОВАЯ КАРТОЧКА</span><h2 id="patient-modal-title">Добавить пациента</h2></div><button class="icon-button" aria-label="Закрыть" :disabled="busy" @click="createOpen = false"><X/></button></div><div v-if="error" class="alert error">{{ error }}</div><form class="stack" @submit.prevent="newPatient"><label>ФИО<input v-model="form.name" required minlength="2" placeholder="Фамилия Имя Отчество" autofocus></label><div class="form-grid"><label>ИИН<MaskedInput v-model="form.iin" required/></label><label>Дата рождения<input v-model="form.birth_date" required type="date" :max="new Date().toISOString().slice(0,10)"><small class="hint">{{ birthDateFromIin(form.iin) ? 'Подставлена из ИИН — сверьте с документом; можно исправить.' : 'Введите по документу, если дата не определяется из ИИН.' }}</small></label><label>Телефон <span class="hint">необязательно</span><MaskedInput v-model="form.phone" kind="phone"/></label><label>Пол<select v-model="form.sex"><option value="unknown">Не указан</option><option value="female">Женский</option><option value="male">Мужской</option></select></label></div><label>Идентификатор в МИС <span class="hint">необязательно</span><input v-model="form.external_id" placeholder="Внешний ID пациента"></label><div class="consent-box"><ShieldCheck :size="21"/><h4>Согласие пациента</h4><label v-if="!signatureRequired" class="check"><input v-model="form.processing_consent" type="checkbox">Пациент согласен на запись и обработку данных, включая передачу аудио в OpenAI; решение зафиксировано врачом</label><p v-if="signatureRequired" class="hint">После создания карты пациент подпишет согласие своей ЭЦП через NCALayer или QR / eGov Mobile. До подписания доступно ручное заполнение.</p><p v-else class="hint">Эта отметка не является электронной подписью. После создания карты можно оформить ЭЦП пациента.</p><details class="consent-details"><summary>Что включает согласие</summary><p>Запись и распознавание разговора, передачу обезличенного текста облачной LLM, проверенного обезличенного аудио облачному ASR и исходной аудиозаписи в OpenAI, включая возможные персональные и медицинские данные. При отправке исходной записи в OpenAI маскирование выполняется после распознавания.</p></details><p class="hint">Без согласия врач заполнит лист консультации вручную.</p></div><button class="primary full" :disabled="busy">Создать карточку<ArrowUpRight :size="18"/></button></form></section></div>
  </div>
</template>
