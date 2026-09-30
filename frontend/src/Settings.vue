<script setup>
import { ref, onMounted } from 'vue'
import { ShieldCheck, PlugZap, Cpu, KeyRound, Mic, Clipboard, Trash2 } from 'lucide-vue-next'
import { api } from './api'
import { useRecorder } from './recorder'
import IdentitySigning from './IdentitySigning.vue'
defineProps(['settings'])
const key = ref(''), error = ref(''), notice = ref(''), busy = ref(false)
const rec = useRecorder(), { devices, selected } = rec
const profile = ref(null), identityEnabled = ref(false)
onMounted(() => run(async () => { profile.value = await api('/auth/me'); identityEnabled.value = (await api('/auth/options')).sigex_enabled }))
function linked(value) {
  profile.value = value
  notice.value = 'ИИН подтверждён. Через ЭЦП и eGov Mobile вы войдёте в этот же кабинет.'
}
async function run(fn) { busy.value = true; error.value = ''; try { await fn() } catch (e) { error.value = e.message } finally { busy.value = false } }
async function createKey() { const result = await api('/integration-key', { method: 'POST' }); key.value = result.api_key; notice.value = result.note }
async function revoke() { await api('/integration-key', { method: 'DELETE' }); key.value = ''; notice.value = 'API-ключ отозван' }
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">ЛИЧНЫЙ КАБИНЕТ</span><h1>Настройки</h1><p class="muted">Микрофон, обработка данных и подключение МИС.</p></div></div>
  <div v-if="error" class="alert error">{{ error }}</div><div v-if="notice" class="alert success">{{ notice }}</div>
  <div class="settings-grid">
    <section class="panel settings-card"><div class="settings-icon"><KeyRound/></div><h3>ИИН и вход по ЭЦП</h3><p v-if="profile?.iin" class="muted">ИИН: {{ profile.iin }} · {{ profile.iin_verified ? 'Подтверждён подписью' : 'Указан при регистрации' }}</p><p v-else class="muted">В этой учётной записи ещё нет ИИН. Привяжите его своей ЭЦП, чтобы входить через ЭЦП или eGov Mobile в тот же кабинет.</p><IdentitySigning :enabled="identityEnabled" purpose="link" @complete="linked"/><p class="hint">ИИН берётся из подписи XML, проверенной SIGEX. Привязка другого ИИН или объединение разных кабинетов не выполняется.</p></section>
    <section class="panel settings-card"><div class="settings-icon"><Mic/></div><h3>Микрофон</h3><p class="muted">Разрешите доступ и выберите источник звука. Выбор сохраняется в этом браузере.</p><button class="secondary" @click="run(rec.discover)">Разрешить доступ</button><label>Источник звука<select v-model="selected" @change="rec.saveDevice"><option value="">Системный микрофон</option><option v-for="d in devices" :value="d.deviceId" :key="d.deviceId">{{ d.label }}</option></select></label></section>
    <section class="panel settings-card"><div class="settings-icon"><Cpu/></div><h3>Модели обработки</h3><dl><dt>Распознавание</dt><dd>{{ settings.asr_provider === 'disabled' ? 'Не подключено' : settings.asr_provider }}</dd><dt>Модель ASR</dt><dd>{{ settings.asr_model }}</dd><dt>Готовность ASR</dt><dd>{{ settings.asr_configured ? 'Настроено' : settings.asr_provider === 'openai' ? 'Ожидает OPENAI_API_KEY' : 'Не настроено' }}</dd><dt>Генерация текста</dt><dd>{{ settings.llm_provider === 'disabled' ? 'Не подключена' : settings.llm_provider }}</dd><dt>Модель LLM</dt><dd>{{ settings.llm_model }}</dd><dt>Режим LLM</dt><dd>{{ settings.llm_is_cloud ? 'Облачный' : 'Свой сервер / локально' }}</dd></dl><p class="hint">Администратор задаёт провайдеров и ключи в серверном .env. Секреты не передаются в браузер.</p></section>
    <section class="panel settings-card"><div class="settings-icon"><PlugZap/></div><h3>Интеграция с МИС</h3><p class="muted">МИС может получать подтверждённые приёмы по API. Ключ открывает доступ только к данным вашего кабинета.</p><code>GET /api/v1/integration/encounters</code><p><a href="/api/docs" target="_blank" rel="noopener">Документация API ↗</a></p><div class="inline-actions"><button class="secondary" :disabled="busy" @click="run(createKey)"><KeyRound :size="16"/>Выпустить новый ключ</button><button class="icon-button" aria-label="Отозвать ключ МИС" :disabled="busy" @click="run(revoke)"><Trash2 :size="18"/></button></div><div v-if="key" class="key-result"><code>{{ key }}</code><button class="text-button" @click="run(async () => { await navigator.clipboard.writeText(key); notice = 'Ключ скопирован' })"><Clipboard :size="15"/>Скопировать</button></div></section>
    <section class="panel settings-card"><div class="settings-icon"><ShieldCheck/></div><h3>Конфиденциальность</h3><ul class="privacy-list"><li>Запись только при согласии пациента.</li><li>Персональные данные и тексты зашифрованы в БД.</li><li v-if="settings.asr_provider === 'openai'">OpenAI получает исходную запись только при согласии пациента на запись и обработку данных с помощью ИИ. Маскирование — после распознавания.</li><li v-else>Исходное аудио обрабатывается на настроенном ASR-сервере.</li><li>Перед LLM текст автоматически маскируется; врач проверяет полноту маскирования и результат.</li><li>Исходные аудиозаписи, расшифровки и версии заключений сохраняются зашифрованно.</li></ul></section>
  </div>
</template>
