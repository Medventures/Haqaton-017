import test from 'node:test'
import assert from 'node:assert/strict'
import { iinDigits, formatIin, normalizePhone, formatPhone, birthDateFromIin } from '../src/patient-input.js'

test('маски оставляют в модели только канонические реквизиты', () => {
  assert.equal(iinDigits('900101 300001'), '900101300001')
  assert.equal(formatIin('900101300001'), '900101 300001')
  assert.equal(iinDigits('abc90010130000199'), '900101300001')
  for (const phone of ['+7 (701) 123-45-67', '87011234567', '7011234567', '+77011234567']) {
    assert.equal(normalizePhone(phone), '+77011234567')
    assert.equal(formatPhone(phone), '+7 (701) 123-45-67')
  }
  assert.equal(formatPhone(''), '')
  assert.equal(normalizePhone('+7 ('), '')
  assert.equal(formatPhone('+7701'), '+7 (701)')
})

test('дата рождения из ИИН: век, високосный день и ручной ввод при неоднозначности', () => {
  const today = new Date(2026, 8, 30)
  assert.equal(birthDateFromIin('900101300001', today), '1990-01-01')
  assert.equal(birthDateFromIin('000229600001', today), '2000-02-29')
  for (const iin of ['010229500001', '990101500001', '900101900001', '900132300001', '900101', '900101100001']) {
    assert.equal(birthDateFromIin(iin, today), '')
  }
})

test('пустой телефон разрешён, неправильный номер не исправляется незаметно', async () => {
  const { phoneError } = await import('../src/patient-input.js')
  for (const value of ['', '   ', null, '+7 (701) 123-45-67', '87011234567', '7011234567']) assert.equal(phoneError(value), '')
  for (const value of ['abc7011234567', '+17011234567', '+770112345670', '+7 (701) 12']) {
    assert.match(phoneError(value), /Невалидный телефон/)
    assert.match(phoneError(formatPhone(value)), /Невалидный телефон/)
  }
  assert.equal(normalizePhone('   '), '')
})
