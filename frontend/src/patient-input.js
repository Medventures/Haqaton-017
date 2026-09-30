export const iinDigits = value => String(value || '').replace(/\D/g, '').slice(0, 12)
export function formatIin(value) {
  const digits = iinDigits(value)
  return digits.length > 6 ? digits.slice(0, 6) + ' ' + digits.slice(6) : digits
}
export function normalizePhone(value) {
  const raw = String(value ?? '').trim()
  if (!raw) return ''
  if (!/^\+?[0-9\s()-]*$/.test(raw) || (raw.startsWith('+') && !raw.startsWith('+7'))) return raw
  let digits = raw.replace(/\D/g, '')
  if (digits.length > 11 || (digits.length === 11 && !/^[78]/.test(digits))) return raw
  if (raw.startsWith('+7') || (digits.length === 11 && /^[78]/.test(digits))) digits = digits.slice(1)
  if (digits.length > 10) return raw
  return digits ? '+7' + digits : ''
}
export function phoneError(value) {
  const raw = String(value ?? '').trim()
  return !raw || /^\+7[0-9]{10}$/.test(normalizePhone(raw)) ? '' : 'Невалидный телефон. Введите номер в формате +7 (701) 123-45-67 или оставьте поле пустым.'
}
export function formatPhone(value) {
  const normalized = normalizePhone(value)
  if (!normalized) return ''
  if (!/^\+7[0-9]{1,10}$/.test(normalized)) return normalized
  const n = normalized.slice(2)
  let result = '+7 (' + n.slice(0, 3)
  if (n.length >= 3) result += ')'
  if (n.length > 3) result += ' ' + n.slice(3, 6)
  if (n.length > 6) result += '-' + n.slice(6, 8)
  if (n.length > 8) result += '-' + n.slice(8, 10)
  return result
}
export function birthDateFromIin(value, today = new Date()) {
  const iin = iinDigits(value)
  if (iin.length !== 12 || !/[1-6]/.test(iin[6])) return ''
  const year = 1800 + Math.floor((Number(iin[6]) - 1) / 2) * 100 + Number(iin.slice(0, 2))
  const month = Number(iin.slice(2, 4)), day = Number(iin.slice(4, 6))
  const date = new Date(Date.UTC(year, month - 1, day))
  if (year < 1900 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return ''
  const result = date.toISOString().slice(0, 10)
  const limit = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  return result <= limit ? result : ''
}
