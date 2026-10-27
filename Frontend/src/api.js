import { clientApiPath, clientSlug } from './clientRouting.js'
import { MAX_PHOTOS } from './chatPhoto.js'

export async function sendChatMessage(message, { slug = clientSlug(), signal, images, image } = {}) {
  if (images !== undefined && image != null) throw new Error('Send images or image, not both')
  const attachments = images ?? (image ? [image] : [])
  if (!Array.isArray(attachments) || attachments.length > MAX_PHOTOS) {
    throw new Error(`Send at most ${MAX_PHOTOS} photos per message`)
  }
  const response = await fetch(clientApiPath('chat', slug), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    signal,
    body: JSON.stringify({ message, ...(attachments.length ? { images: attachments.map(({ mime_type, data }) => ({ mime_type, data })) } : {}) }),
  })

  if (!response.ok) {
    let detail = ''
    try {
      const serverDetail = (await response.json()).detail
      detail = typeof serverDetail === 'string' ? serverDetail : serverDetail?.map((item) => item.msg).join('; ')
    } catch {
      detail = ''
    }
    throw new Error(detail || `Chat failed (${response.status})`)
  }

  return response.json()
}
