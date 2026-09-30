export async function api(path, options = {}) {
  const headers = { 'X-Medhub-Request': '1', ...options.headers }
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(options.body)
  }
  let response
  try { response = await fetch('/api/v1' + path, { credentials: 'same-origin', ...options, headers }) }
  catch { throw new Error('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.') }
  let result
  try { result = await response.json() } catch { throw new Error('Сервер недоступен. Повторите попытку.') }
  if (!response.ok) {
    const detail = result.detail
    const error = new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(x => `${x.loc.at(-1)}: ${x.msg}`).join('; ') : 'Не удалось выполнить запрос')
    error.status = response.status
    throw error
  }
  return result
}
