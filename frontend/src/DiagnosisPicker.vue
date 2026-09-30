<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { api } from './api'
const props = defineProps(['code', 'disabled', 'suggestions'])
const emit = defineEmits(['select'])
const query = ref(''), results = ref([]), busy = ref(false), error = ref(''), version = ref(''), searched = ref(false)
let timer, sequence = 0
watch(query, () => {
  clearTimeout(timer); const request = ++sequence
  results.value = []; searched.value = false; busy.value = false; error.value = ''
  if (query.value.trim().length < 2) return
  timer = setTimeout(async () => {
    busy.value = true; error.value = ''
    try {
      const data = await api('/diagnoses?q=' + encodeURIComponent(query.value))
      if (request === sequence) { results.value = data.items; version.value = data.version; searched.value = true }
    } catch (e) { if (request === sequence) error.value = e.message }
    finally { if (request === sequence) busy.value = false }
  }, 250)
})
function select(item) { emit('select', item); query.value = ''; results.value = [] }
onBeforeUnmount(() => { clearTimeout(timer); sequence++ })
</script>
<template>
  <div class="diagnosis-picker">
    <label>Поиск в МКБ-10<input v-model="query" :disabled="disabled" placeholder="Код, название, сокращение: I10, гипертония…" autocomplete="off" aria-label="Поиск диагноза"></label>
    <p v-if="busy" class="hint" role="status">Поиск…</p><p v-if="error" class="alert error">{{ error }}</p>
    <div v-if="results.length" class="diagnosis-results" aria-label="Результаты поиска диагнозов"><button v-for="item in results" :key="item.code" type="button" :disabled="disabled" @click="select(item)"><strong>{{ item.code }}</strong><span>{{ item.name }}</span></button></div>
    <p v-if="searched && !results.length && !busy" class="hint">Не найдено. Попробуйте часть названия или код.</p>
    <p class="hint">{{ code ? 'Выбран код: ' + code + '. ' : '' }}МКБ-10 · срез 14.04.2021. Справочник требует сверки с используемой клиникой версией.</p>
    <div v-if="suggestions?.length" class="diagnosis-suggestions"><strong>Предложения ИИ · не диагноз</strong><button v-for="item in suggestions" :key="item.code" :disabled="disabled" type="button" @click="select(item)"><span>{{ item.code }} · {{ item.name }}</span><small>{{ item.reason }}</small><b>Выбрать для проверки</b></button></div>
  </div>
</template>
