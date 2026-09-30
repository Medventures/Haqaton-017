<script setup>
import MaskedInput from './MaskedInput.vue'
import { ref, onMounted } from 'vue'
import { ArrowRight, ShieldCheck, Stethoscope, KeyRound } from 'lucide-vue-next'
import { api } from './api'
import IdentitySigning from './IdentitySigning.vue'
import UmcLogo from './UmcLogo.vue'
const emit = defineEmits(['login'])
const register = ref(false), busy = ref(false), error = ref(''), options = ref({})
const form = ref({ name: '', iin: '', email: '', phone: '', password: '', code: '' })
onMounted(async () => { try { options.value = await api('/auth/options') } catch (e) { error.value = e.message } })
async function submit() {
  error.value = ''; busy.value = true
  try { emit('login', await api('/auth/' + (register.value ? 'register' : 'login'), { method: 'POST', body: register.value ? form.value : { email: form.value.email, password: form.value.password } })) }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <div class="auth-layout">
    <section class="auth-story">
      <a class="platform-brand" href="/"><UmcLogo :caption="false"/><strong>Smart Consult</strong><span>Консультация с AI / ИИ-ассистентом</span></a>
      <div class="story-copy"><span class="eyebrow light">БОЛЬШЕ ВНИМАНИЯ ПАЦИЕНТУ</span><h1>Вы ведёте приём.<br><span>Мы помогаем<br>с записями.</span></h1><p>AI-ассистент превращает разговор в структурированный лист консультации. Решение всегда остаётся за врачом.</p></div>
      <div class="story-note"><div class="note-top"><Stethoscope :size="22"/><span>Ваш помощник на приёме</span><span class="small-dot"></span></div><div class="note-lines"><i></i><i></i><i></i></div><div class="note-bottom"><ShieldCheck :size="17"/> С согласия пациента. Под контролем врача.</div></div>
      <footer>Smart Consult · кабинет врача <span>Создано в рамках хакатона medhub</span></footer>
    </section>
    <section class="auth-form">
      <div class="auth-form-inner">
        <a class="platform-brand auth-mobile-brand" href="/"><UmcLogo :caption="false"/><strong>Smart Consult</strong><span>Консультация с AI / ИИ-ассистентом</span></a>
        <span class="eyebrow">ЛИЧНЫЙ КАБИНЕТ ВРАЧА</span><h2>{{ register ? 'Начнём знакомство' : 'Рады видеть вас' }}</h2><p class="muted">{{ register ? 'Создайте учётную запись врача' : 'Войдите, чтобы продолжить работу с пациентами' }}</p>
        <div class="tabs"><button :class="{ active: !register }" @click="register = false">Вход</button><button :class="{ active: register }" @click="register = true">Регистрация</button></div>
        <div v-if="error" class="alert error" role="alert">{{ error }}</div>
        <form @submit.prevent="submit" class="stack">
          <label v-if="register">ФИО врача<input v-model="form.name" required minlength="2" autocomplete="name" placeholder="Как к вам обращаться"></label>
          <label v-if="register">ИИН врача<MaskedInput v-model="form.iin" required autocomplete="off" aria-describedby="iin-help"/></label>
          <p v-if="register" id="iin-help" class="hint">Для входа через ЭЦП или eGov Mobile ИИН в проверенной подписи должен совпадать с ИИН учётной записи.</p>
          <label>Электронная почта<input v-model="form.email" type="email" required autocomplete="username" placeholder="doctor@clinic.kz"></label>
          <label v-if="register">Телефон <span class="hint">необязательно</span><MaskedInput v-model="form.phone" kind="phone" autocomplete="tel"/></label>
          <label>Пароль<input v-model="form.password" type="password" required :minlength="register ? 12 : 1" :autocomplete="register ? 'new-password' : 'current-password'" placeholder="Введите пароль"></label>
          <label v-if="register && !options.demo_mode">Код приглашения<input v-model="form.code" autocomplete="off" placeholder="Код от администратора клиники"></label>
          <p v-if="register" class="hint">Не менее 12 символов. Регистрация по ЭЦП также доступна ниже.</p>
          <button class="primary full" :disabled="busy">{{ busy ? 'Подождите…' : register ? 'Создать кабинет' : 'Войти в кабинет' }}<ArrowRight :size="18"/></button>
        </form>
        <div class="divider">или {{ register ? 'зарегистрируйтесь' : 'войдите' }} по ЭЦП</div>
        <IdentitySigning :enabled="!!options.sigex_enabled" :purpose="register ? 'register' : 'login'" :iin="register ? form.iin : ''" :code="form.code" @complete="emit('login', $event)"/>
        <p class="auth-security"><KeyRound :size="15"/> Подпись проверяется на сервере SIGEX</p>
        <p v-if="options.demo_mode" class="demo-note">Локальная демонстрация · используйте вымышленные данные</p>
      </div>
    </section>
  </div>
</template>
