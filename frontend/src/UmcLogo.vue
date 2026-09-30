<script setup>
import horizontalLogo from './assets/umc-horizontal.png'
import stackedLogo from './assets/umc-stacked.png'

defineProps({ compact: Boolean, caption: { type: Boolean, default: true } })
</script>

<template>
  <div class="umc-branding" :class="{ 'umc-branding-compact': compact }">
    <img :src="compact ? stackedLogo : horizontalLogo" alt="UMC — University Medical Center" :width="compact ? 4499 : 9121" :height="compact ? 2912 : 1322">
    <span v-if="caption">{{ compact ? 'Хакатон medhub' : 'Создано в рамках хакатона medhub' }}</span>
  </div>
</template>
