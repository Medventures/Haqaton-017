// Размер окна не определяет устройство: узкое окно ПК всё ещё поддерживает NCALayer.
const device = globalThis.navigator
export const mobileSigning = Boolean(device && (
  device.userAgentData?.mobile || /Android|iPhone|iPad|iPod/i.test(device.userAgent) ||
  (device.platform === 'MacIntel' && device.maxTouchPoints > 1)
))
