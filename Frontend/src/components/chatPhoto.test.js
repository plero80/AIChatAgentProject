import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { readChatPhoto, readChatPhotos, MAX_PHOTO_BYTES, MAX_PHOTOS } from '../chatPhoto.js'
import { sendChatMessage } from '../api.js'

const server = await createServer({ server: { middlewareMode: true, watch: null }, logLevel: 'silent' })
after(() => server.close())
const { default: ChatInput } = await server.ssrLoadModule('/src/components/ChatInput.jsx')
const { default: MessageList } = await server.ssrLoadModule('/src/components/MessageList.jsx')

test('rejects unsupported and oversized files before reading them', async () => {
  await assert.rejects(readChatPhoto({ type: 'image/svg+xml', size: 100 }), /JPEG/)
  await assert.rejects(readChatPhoto({ type: 'image/png', size: MAX_PHOTO_BYTES + 1 }), /5 MB/)
  await assert.rejects(readChatPhoto({ type: 'image/jpeg', size: 0 }), /5 MB/)
})

test('reads photo for preview and transport, and cancels an in-flight read', async (t) => {
  const original = globalThis.FileReader
  class Reader {
    readAsDataURL(file) {
      if (file.name === 'pending') return
      this.result = 'data:image/png;base64,aW1hZ2U='
      queueMicrotask(() => this.onload())
    }
    abort() { this.onabort() }
  }
  globalThis.FileReader = Reader
  t.after(() => { globalThis.FileReader = original })
  const image = await readChatPhoto({ type: 'image/png', size: 5, name: 'food.png' })
  assert.deepEqual(image, { mime_type: 'image/png', data: 'aW1hZ2U=', preview: 'data:image/png;base64,aW1hZ2U=', name: 'food.png' })
  const controller = new AbortController()
  const waiting = readChatPhoto({ type: 'image/png', size: 5, name: 'pending' }, {}, controller.signal)
  controller.abort()
  await assert.rejects(waiting, { name: 'AbortError' })
})

test('API sends photo-only and combined turns without local preview or filename', async (t) => {
  const bodies = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/clients/chef/chat')
    bodies.push(JSON.parse(options.body))
    return { ok: true, json: async () => ({ reply: 'Recipe' }) }
  })
  const image = { mime_type: 'image/png', data: 'aW1hZ2U=', name: 'private filename.png', preview: 'data:preview' }
  await sendChatMessage('', { slug: 'chef', image })
  await sendChatMessage('Salty recipe', { slug: 'chef', image })
  assert.deepEqual(bodies, [
    { message: '', images: [{ mime_type: 'image/png', data: 'aW1hZ2U=' }] },
    { message: 'Salty recipe', images: [{ mime_type: 'image/png', data: 'aW1hZ2U=' }] },
  ])
})

test('composer exposes a labelled upload only for photo-enabled conversations', () => {
  const enabled = renderToStaticMarkup(createElement(ChatInput, { imageUpload: { enabled: true } }))
  assert.match(enabled, /type="file"/)
  assert.match(enabled, /multiple=""/)
  assert.match(enabled, /accept="image\/jpeg,image\/png,image\/webp"/)
  assert.match(enabled, /aria-label="צירוף תמונות מצרכים"/)
  assert.match(enabled, /0\/10/)
  const disabled = renderToStaticMarkup(createElement(ChatInput, { imageUpload: { enabled: false } }))
  assert.doesNotMatch(disabled, /type="file"/)
})

test('conversation shows an attached photo alongside text and failed-send status', () => {
  const html = renderToStaticMarkup(createElement(MessageList, { messages: [
    { id: 'one', role: 'user', content: 'Salty recipe', image: 'data:image/png;base64,aW1hZ2U=', failed: true },
  ] }))
  assert.match(html, /class="user-photo"/)
  assert.match(html, /data:image\/png;base64,aW1hZ2U=/)
  assert.match(html, /Salty recipe/)
  assert.match(html, /class="failed-message"/)
})

test('reads ten photos in order, including additions across selections', async (t) => {
  const original = globalThis.FileReader
  let reads = 0
  class Reader {
    readAsDataURL(file) {
      reads++
      this.result = `data:image/png;base64,${file.name}`
      queueMicrotask(() => this.onload())
    }
  }
  globalThis.FileReader = Reader
  t.after(() => { globalThis.FileReader = original })
  const files = Array.from({ length: 10 }, (_, index) => ({ type: 'image/png', size: 5, name: `photo-${index}` }))
  const first = await readChatPhotos(files.slice(0, 4))
  const rest = await readChatPhotos(files.slice(4), { existingCount: first.length })
  assert.deepEqual([...first, ...rest].map((photo) => photo.data), files.map((file) => file.name))
  assert.equal(reads, 10)
  await assert.rejects(readChatPhotos([files[0]], { existingCount: 10 }), /10/)
  await assert.rejects(readChatPhotos([...files, files[0]]), /10/)
  await assert.rejects(readChatPhotos(files, { options: { max_count: 20 } , existingCount: 1 }), /10/)
  await assert.rejects(readChatPhotos(files, { options: { max_count: 3 } }), /3/)
  assert.equal(reads, 10, 'excess selections must not read or silently drop files')
})

test('rejects the entire selection if its last file is invalid', async (t) => {
  const original = globalThis.FileReader
  globalThis.FileReader = class { constructor() { assert.fail('must validate the whole batch before reading') } }
  t.after(() => { globalThis.FileReader = original })
  const valid = { type: 'image/png', size: 5 }
  await assert.rejects(readChatPhotos([valid, { type: 'image/png', size: MAX_PHOTO_BYTES + 1 }]), /5 MB/)
  await assert.rejects(readChatPhotos([valid, { type: 'image/svg+xml', size: 5 }]), /JPEG/)
})

test('cancelling a batch stops subsequent reads and does not return a partial batch', async (t) => {
  const original = globalThis.FileReader
  const controller = new AbortController()
  const reads = []
  class Reader {
    readAsDataURL(file) {
      reads.push(file.name)
      if (file.name === 'cancel') return queueMicrotask(() => controller.abort())
      this.result = 'data:image/png;base64,aW1hZ2U='
      queueMicrotask(() => this.onload())
    }
    abort() { this.onabort() }
  }
  globalThis.FileReader = Reader
  t.after(() => { globalThis.FileReader = original })
  const files = ['first', 'cancel', 'never'].map((name) => ({ type: 'image/png', size: 5, name }))
  await assert.rejects(readChatPhotos(files, { signal: controller.signal }), { name: 'AbortError' })
  assert.deepEqual(reads, ['first', 'cancel'])
})

test('API sends all ten photos in one message and rejects an eleventh before fetch', async (t) => {
  const bodies = []
  t.mock.method(globalThis, 'fetch', async (_, options) => {
    bodies.push(JSON.parse(options.body))
    return { ok: true, json: async () => ({ reply: 'Recipe' }) }
  })
  const images = Array.from({ length: MAX_PHOTOS }, (_, index) => ({
    mime_type: 'image/png', data: `photo${index}`, preview: 'private preview', name: 'private filename', id: index,
  }))
  for (const message of ['', 'Something salty']) {
    await sendChatMessage(message, { images })
    assert.deepEqual(bodies.at(-1), { message, images: images.map(({ mime_type, data }) => ({ mime_type, data })) })
  }
  await assert.rejects(sendChatMessage('', { images: [...images, images[0]] }), /10/)
  await assert.rejects(sendChatMessage('', { images, image: images[0] }), /not both/)
  assert.equal(bodies.length, 2)
})

test('conversation displays every photo in a multiple-photo message', () => {
  const images = Array.from({ length: 10 }, (_, index) => `data:image/png;base64,photo${index}`)
  const html = renderToStaticMarkup(createElement(MessageList, { messages: [
    { id: 'ten', role: 'user', content: 'Something salty', images },
  ] }))
  assert.equal((html.match(/class="user-photo"/g) || []).length, 10)
  for (const image of images) assert.ok(html.includes(`src="${image}"`))
  assert.match(html, /Something salty/)
})
