function signAtUrl(url, data, { signal, document = false, xml = false } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error('Подписание отменено')); return }
    const socket = new WebSocket(url)
    let connected = false
    let settled = false
    const finish = (error, result) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', cancel)
      socket.close()
      error ? reject(error) : resolve(result)
    }
    const cancel = () => finish(new Error('Подписание отменено'))
    const unavailable = () => Object.assign(new Error('Запустите NCALayer и разрешите соединение с ним'), { code: 'connection_unavailable' })
    let timer = setTimeout(() => finish(unavailable()), 5000)
    signal?.addEventListener('abort', cancel, { once: true })
    socket.onerror = () => finish(connected ? new Error('Соединение с NCALayer прервано') : unavailable())
    socket.onclose = () => finish(connected ? new Error('Соединение с NCALayer закрыто') : unavailable())
    socket.onopen = () => {
      if (settled) return
      connected = true
      clearTimeout(timer)
      timer = setTimeout(() => finish(new Error('Время подписания истекло')), 120000)
      socket.send(JSON.stringify({ module: 'kz.gov.pki.knca.basics', method: 'sign', args: {
      format: xml ? 'xml' : 'cms', data, signingParams: xml ? {} : { decode: true, encapsulate: true, digested: false, ...(document ? { tsaProfile: {} } : {}) },
      signerParams: { extKeyUsageOids: document ? ['1.3.6.1.5.5.7.3.4', '1.2.398.3.3.4.1.1'] : ['1.2.398.3.3.4.1.1'] }, locale: 'ru'
    } }))
    }
    socket.onmessage = ({ data }) => {
      try {
        const response = JSON.parse(data)
        if (response.result?.version) return
        if (!response.status) return finish(new Error('Подписание отменено или ключ недоступен'))
        const result = response.body?.result
        const signatures = result?.signatures ?? result
        const signature = Array.isArray(signatures) ? signatures[0] : signatures
        if ((Array.isArray(signatures) && signatures.length !== 1) || typeof signature !== 'string' || !signature.trim()) throw new Error()
        finish(null, signature)
      } catch { finish(new Error('Некорректный ответ NCALayer')) }
    }
  })
}

async function signCms(data, options = {}) {
  try { return await signAtUrl('wss://localhost:13579/', data, options) }
  catch (error) {
    if (error.code !== 'connection_unavailable' || options.signal?.aborted) throw error
    return signAtUrl('wss://127.0.0.1:13579/', data, options)
  }
}

// Вход подписывает XML; согласие — CMS с точными байтами PDF.
export const signData = (data, options = {}) => signCms(data, { ...options, document: true })
export const signNonce = nonce => signCms(nonce)
export const signXml = (data, options = {}) => signCms(data, { ...options, xml: true })
