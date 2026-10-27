export const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp']
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024
export const MAX_PHOTOS = 10

export function photoLimit(options = {}) {
  return Math.min(options.max_count ?? MAX_PHOTOS, MAX_PHOTOS)
}

function validatePhoto(file, options) {
  const maxBytes = Math.min(options.max_bytes || MAX_PHOTO_BYTES, MAX_PHOTO_BYTES)
  const types = options.mime_types || PHOTO_TYPES
  if (!PHOTO_TYPES.includes(file.type) || !types.includes(file.type)) {
    throw new Error('אפשר להעלות תמונה מסוג JPEG, PNG או WebP.')
  }
  if (!file.size || file.size > maxBytes) {
    throw new Error('יש לבחור תמונות בגודל של עד 5 MB לכל תמונה.')
  }
}

export async function readChatPhotos(files, { existingCount = 0, options = {}, signal } = {}) {
  const selected = Array.from(files)
  const limit = photoLimit(options)
  if (existingCount + selected.length > limit) {
    throw new Error(`אפשר לצרף עד ${limit} תמונות להודעה. הסירי תמונה כדי להוסיף אחרת.`)
  }
  // Validate the entire selection first; don't silently drop excess/invalid files.
  selected.forEach((file) => validatePhoto(file, options))
  const photos = []
  for (const file of selected) photos.push(await readChatPhoto(file, options, signal))
  return photos
}

export function readChatPhoto(file, options = {}, signal) {
  try {
    validatePhoto(file, options)
  } catch (error) {
    return Promise.reject(error)
  }
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Cancelled', 'AbortError'))
    const reader = new FileReader()
    const cleanup = () => signal?.removeEventListener('abort', abort)
    const abort = () => reader.abort()
    reader.onload = () => {
      cleanup()
      const preview = String(reader.result)
      resolve({ mime_type: file.type, data: preview.slice(preview.indexOf(',') + 1), preview, name: file.name })
    }
    reader.onerror = () => { cleanup(); reject(new Error('לא הצלחנו לקרוא את התמונה. נסי תמונה אחרת.')) }
    reader.onabort = () => { cleanup(); reject(new DOMException('Cancelled', 'AbortError')) }
    signal?.addEventListener('abort', abort, { once: true })
    reader.readAsDataURL(file)
  })
}
