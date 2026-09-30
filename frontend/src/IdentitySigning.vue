<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from './api'
import { signXml } from './eds'
import SigningMethods from './SigningMethods.vue'
import { mobileSigning } from './signingDevice'
const props = defineProps({ enabled: Boolean, purpose: { default: 'login' }, iin: { default: '' }, code: { default: '' } })
const emit = defineEmits(['complete'])
const busy = ref(false), attempt = ref(null), error = ref('')
let timer, stopped = false, polling = false, controller, generation = 0
const storage = 'smartconsult-signing-attempt'
function forget() { sessionStorage.removeItem(storage); attempt.value = null; clearTimeout(timer) }
async function finish(id) {
  const profile = await api(`/auth/identity/${id}/finish`, { method: 'POST' })
  forget(); if (!stopped) emit('complete', profile)
}
async function poll() {
  clearTimeout(timer)
  if (stopped || polling || !attempt.value) return
  polling = true
  const token = generation, id = attempt.value.id
  try {
    const status = await api(`/auth/identity/${id}`)
    if (stopped || token !== generation) return
    attempt.value = { ...attempt.value, ...status }; error.value = status.error || ''
    if (status.state === 'signed') await finish(status.id)
  } catch (e) {
    if (!stopped && token === generation) { error.value = e.message; if ([401, 403, 409, 410].includes(e.status)) forget() }
  } finally {
    polling = false
    if (!stopped && attempt.value) timer = setTimeout(poll, 3000)
  }
}
async function start(method) {
  generation++
  error.value = ''; busy.value = true; forget(); controller?.abort(); controller = new AbortController()
  try {
    const session = await api(`/auth/identity/${method === 'eds' ? 'eds' : 'qr'}/start`, { method: 'POST', body: { purpose: props.purpose, iin: props.iin, code: props.code } })
    if (stopped) return
    if (method === 'eds') {
      const signature = await signXml(session.document_xml, { signal: controller.signal })
      const result = await api(`/auth/identity/${session.id}/signature`, { method: 'POST', body: { signature } })
      if (result.state === 'signed') await finish(session.id)
      else {
        attempt.value = { ...session, ...result }
        sessionStorage.setItem(storage, JSON.stringify({ id: session.id, purpose: props.purpose }))
        timer = setTimeout(poll, 1500)
      }
    } else {
      attempt.value = session; sessionStorage.setItem(storage, JSON.stringify({ id: session.id, purpose: props.purpose }))
      timer = setTimeout(poll, 1500)
    }
  } catch (e) { if (!stopped) error.value = e.message }
  finally { if (!stopped) busy.value = false }
}
function resume() { if (globalThis.document.visibilityState === 'visible' && attempt.value) poll() }
onMounted(async () => {
  window.addEventListener('focus', resume); globalThis.document.addEventListener('visibilitychange', resume)
  try {
    const saved = JSON.parse(sessionStorage.getItem(storage) || 'null')
    if (saved && (saved.purpose === props.purpose || (props.purpose === 'login' && saved.purpose === 'register'))) { attempt.value = saved; await poll() }
  } catch { forget() }
})
onBeforeUnmount(() => { stopped = true; clearTimeout(timer); controller?.abort(); window.removeEventListener('focus', resume); globalThis.document.removeEventListener('visibilitychange', resume) })
</script>
<template>
  <SigningMethods :disabled="busy || !enabled" @select="start"/>
  <p v-if="busy" role="status">Подписание XML и проверка сертификата…</p>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <div v-if="attempt" class="qr-box">
    <img v-if="!mobileSigning && attempt.qr_image && attempt.state !== 'verifying'" :src="attempt.qr_image" alt="QR для подписания XML в eGov Mobile">
    <p v-if="attempt.state === 'verifying'" role="status">Подпись получена. Проверяем сертификат и документ в SIGEX…</p>
    <p v-else>{{ mobileSigning ? 'Откройте eGov Mobile по ссылке и подпишите XML подтверждения входа.' : 'Подпишите XML подтверждения входа. Отсканируйте QR телефоном.' }}</p>
    <a v-if="mobileSigning && attempt.launch_url && attempt.state !== 'verifying'" class="secondary" :href="attempt.launch_url">Открыть eGov Mobile на этом телефоне</a>
    <button class="text-button" @click="poll">Я подписал — проверить статус</button>
  </div>
</template>
