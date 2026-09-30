// Последовательная доставка с повтором того же номера, без потери не подтверждённых Blob.
export function liveUploader(send, onError = () => {}) {
  const pending = []
  let acknowledged = 0, pumping = null, error = ''
  async function pump() {
    if (pumping) return pumping
    pumping = (async () => {
      while (pending.length) {
        try {
          const answer = await send(pending[0], acknowledged)
          if (answer.sequence !== acknowledged) throw new Error('Сервер не подтвердил номер фрагмента')
          pending.shift(); acknowledged++; error = ''
        } catch (err) { error = err.message; onError(error); break }
      }
    })()
    try { await pumping } finally { pumping = null }
  }
  return {
    add(blob) { pending.push(blob); void pump() },
    retry: pump,
    async drain() { await pump(); if (pending.length) throw new Error(error || 'Остались неотправленные фрагменты') },
    get acknowledged() { return acknowledged }, get pending() { return pending.length },
  }
}
