<script setup>
defineProps(['modelValue', 'label', 'name', 'sources', 'reviewed', 'disabled', 'rows', 'regeneratable'])
defineEmits(['update:modelValue', 'source', 'review', 'regenerate'])
</script>
<template>
  <div class="clinical-field">
    <div class="clinical-field-title"><label :for="'field-' + name">{{ label }}</label><span class="field-state" :class="{ verified: reviewed }">{{ reviewed ? 'Проверено врачом' : modelValue ? (sources?.length ? 'Из диалога · проверить' : 'Без ссылки · проверить') : 'Не уточнено' }}</span></div>
    <textarea :id="'field-' + name" :value="modelValue" :disabled="disabled" :rows="rows || 3" placeholder="Нет сведений — уточните или заполните вручную" @input="$emit('update:modelValue', $event.target.value)"></textarea>
    <button v-if="regeneratable" class="text-button" type="button" :disabled="disabled" :aria-label="'Перегенерировать: ' + label" @click="$emit('regenerate')">↻ Дополнить с ИИ</button>
    <div class="field-links"><button v-for="index in sources || []" :key="index" type="button" class="text-button" @click="$emit('source', index)">Реплика {{ index + 1 }} ↗</button><button v-if="modelValue" class="text-button" type="button" :disabled="disabled" :aria-pressed="!!reviewed" @click="$emit('review')">{{ reviewed ? 'Проверено врачом' : 'Проверить' }}</button></div>
  </div>
</template>
