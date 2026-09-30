<script setup>
import { Fingerprint, QrCode, Smartphone } from 'lucide-vue-next'
import { mobileSigning } from './signingDevice'
defineProps({ disabled: Boolean })
defineEmits(['select'])
</script>
<template>
  <div class="signing-methods">
    <button v-if="!mobileSigning" type="button" class="secondary" :disabled="disabled" @click="$emit('select', 'eds')"><Fingerprint :size="20"/>NCALayer · ЭЦП на ПК</button>
    <button v-if="!mobileSigning" type="button" class="secondary" :disabled="disabled" @click="$emit('select', 'qr')"><QrCode :size="20"/>QR · сканировать телефоном</button>
    <button v-if="mobileSigning" type="button" class="secondary" :disabled="disabled" @click="$emit('select', 'mobile')"><Smartphone :size="20"/>eGov Mobile · на этом телефоне</button>
  </div>
</template>
<style scoped>
.signing-methods{display:flex;flex-wrap:wrap;gap:10px}.signing-methods button{flex:1 1 200px;justify-content:flex-start;text-align:left;font-size:14px}
</style>
