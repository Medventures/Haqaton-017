import test from 'node:test'
import assert from 'node:assert/strict'
import { signData, signNonce, signXml } from '../src/eds.js'

function socketFixture(t) {
  const original = globalThis.WebSocket, sockets = []
  globalThis.WebSocket = class {
    constructor(url) { this.url = url; this.closed = 0; sockets.push(this); queueMicrotask(() => this.onopen?.()) }
    send(value) { this.request = JSON.parse(value) }
    close() { this.closed++; this.onclose?.() }
    respond(value) { this.onmessage?.({ data: JSON.stringify(value) }) }
  }
  t.after(() => { globalThis.WebSocket = original })
  return sockets
}

test('согласие подписывает точные байты PDF с TSA и параметрами подписи документа', async t => {
  const sockets = socketFixture(t), signal = new AbortController()
  const result = signData('JVBERi0xLjcKc3ludGhldGlj', { signal: signal.signal })
  await Promise.resolve()
  const socket = sockets[0]
  assert.equal(socket.url, 'wss://localhost:13579/')
  assert.equal(socket.request.args.data, 'JVBERi0xLjcKc3ludGhldGlj')
  assert.deepEqual(socket.request.args.signingParams, { decode: true, encapsulate: true, digested: false, tsaProfile: {} })
  assert.deepEqual(socket.request.args.signerParams.extKeyUsageOids, ['1.3.6.1.5.5.7.3.4', '1.2.398.3.3.4.1.1'])
  socket.respond({ status: true, body: { result: { signatures: ['synthetic-cms'] } } })
  assert.equal(await result, 'synthetic-cms')
  signal.abort()
  assert.equal(socket.closed, 1, 'Обработчик отмены удалён после завершения')
})

test('signNonce сохраняет прежние параметры авторизации', async t => {
  const sockets = socketFixture(t), result = signNonce('c3ludGhldGljLW5vbmNl')
  await Promise.resolve()
  assert.deepEqual(sockets[0].request.args.signingParams, { decode: true, encapsulate: true, digested: false })
  assert.deepEqual(sockets[0].request.args.signerParams.extKeyUsageOids, ['1.2.398.3.3.4.1.1'])
  sockets[0].respond({ status: true, body: { result: 'nonce-signature' } })
  assert.equal(await result, 'nonce-signature')
})

test('отмена закрывает NCALayer и не возвращает подпись', async t => {
  const sockets = socketFixture(t), controller = new AbortController()
  const result = signData('cGRm', { signal: controller.signal })
  await Promise.resolve()
  controller.abort()
  await assert.rejects(result, /Подписание отменено/)
  sockets[0].respond({ status: true, body: { result: 'late-signature' } })
  assert.equal(sockets[0].closed, 1)
})

test('заранее отменённый запрос не открывает соединение NCALayer', async t => {
  const sockets = socketFixture(t), controller = new AbortController()
  controller.abort()
  await assert.rejects(signData('cGRm', { signal: controller.signal }), /Подписание отменено/)
  assert.equal(sockets.length, 0)
})

test('закрытие NCALayer отклоняет незавершённое подписание', async t => {
  const sockets = socketFixture(t), result = signData('cGRm')
  await Promise.resolve()
  sockets[0].onclose()
  await assert.rejects(result, /Соединение с NCALayer закрыто/)
  assert.equal(sockets[0].closed, 1)
})


test('вход подписывает читаемый XML без base64-декодирования', async t => {
  const sockets = socketFixture(t), xml = '<authentication><purpose>login</purpose></authentication>'
  const result = signXml(xml)
  await Promise.resolve()
  assert.equal(sockets[0].request.args.format, 'xml')
  assert.equal(sockets[0].request.args.data, xml)
  assert.deepEqual(sockets[0].request.args.signingParams, {})
  sockets[0].respond({ status: true, body: { result: '<signed/>' } })
  assert.equal(await result, '<signed/>')
})


test('недоступный localhost переключается на 127.0.0.1 до начала подписания', async t => {
  const original = globalThis.WebSocket, sockets = []
  globalThis.WebSocket = class {
    constructor(url) { this.url=url; sockets.push(this); queueMicrotask(()=>url.includes('localhost') ? this.onerror() : this.onopen()) }
    close() { this.onclose?.() }
    send(value) { this.request=JSON.parse(value); queueMicrotask(()=>this.onmessage({data:JSON.stringify({status:true,body:{result:'synthetic-signature'}})})) }
  }
  t.after(()=>{globalThis.WebSocket=original})
  assert.equal(await signData('cGRm'), 'synthetic-signature')
  assert.deepEqual(sockets.map(x=>x.url),['wss://localhost:13579/','wss://127.0.0.1:13579/'])
})

test('отмена пользователем не открывает второй запрос подписи', async t => {
  const sockets=socketFixture(t), result=signData('cGRm')
  await Promise.resolve()
  sockets[0].respond({status:false,code:'USER_CANCELLED'})
  await assert.rejects(result,/Подписание отменено/)
  assert.equal(sockets.length,1)
})
