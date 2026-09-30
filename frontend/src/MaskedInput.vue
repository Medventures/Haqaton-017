<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { iinDigits, formatIin, normalizePhone, formatPhone, phoneError } from './patient-input'
defineOptions({ inheritAttrs: false })
const props = defineProps({ modelValue: { type: String, default: '' }, kind: { type: String, default: 'iin' } })
const emit = defineEmits(['update:modelValue'])
const inputElement = ref(null), showError = ref(false)
const error = computed(() => props.kind === 'phone' ? phoneError(props.modelValue) : '')
watch(error, async value => { await nextTick(); inputElement.value?.setCustomValidity(value) }, { immediate: true })
function blur(event) { event.target.value = display.value; showError.value = true }
const display = computed(() => props.kind === 'phone' ? formatPhone(props.modelValue) : formatIin(props.modelValue))
function input(event) {
  const el = event.target, raw = el.value, cursor = el.selectionStart
  let count = raw.slice(0, cursor).replace(/\D/g, '').length
  const value = props.kind === 'phone' ? normalizePhone(raw) : iinDigits(raw)
  const formatted = props.kind === 'phone' ? formatPhone(value) : formatIin(value)
  if (props.kind === 'phone' && !raw.startsWith('+7') && raw.replace(/\D/g, '').length <= 10) count++
  let position = 0, digits = 0
  while (position < formatted.length && digits < count) { if (/\d/.test(formatted[position])) digits++; position++ }
  el.setCustomValidity(props.kind === 'phone' ? phoneError(value) : '')
  el.value = formatted
  el.setSelectionRange(position, position)
  emit('update:modelValue', value)
}
function keydown(event) {
  const el = event.target, backwards = event.key === 'Backspace'
  if ((!backwards && event.key !== 'Delete') || el.selectionStart !== el.selectionEnd) return
  let index = el.selectionStart - (backwards ? 1 : 0)
  if (index < 0 || index >= el.value.length || /\d/.test(el.value[index])) return
  while (index >= 0 && index < el.value.length && !/\d/.test(el.value[index])) index += backwards ? -1 : 1
  if (index < 0 || index >= el.value.length || (props.kind === 'phone' && index < 4)) return
  event.preventDefault()
  el.value = el.value.slice(0, index) + el.value.slice(index + 1)
  el.setSelectionRange(index, index)
  input({ target: el })
}
</script>
<template>
  <input ref="inputElement" v-bind="$attrs" :aria-invalid="error && showError ? true : undefined" :type="kind === 'phone' ? 'tel' : 'text'" inputmode="numeric" :value="display" :placeholder="kind === 'phone' ? '+7 (___) ___-__-__' : '______ ______'" :pattern="kind === 'phone' ? '\\+7 \\([0-9]{3}\\) [0-9]{3}-[0-9]{2}-[0-9]{2}' : '[0-9]{6} [0-9]{6}'" @input="input" @keydown="keydown" @blur="blur" @invalid="showError = true">
  <small v-if="error && showError" class="phone-validation-error" role="alert">{{ error }}</small>
</template>
<style scoped>
.phone-validation-error{display:block;color:#A03A35;font-size:13px;line-height:1.5;font-weight:400;margin-top:6px}
</style>
