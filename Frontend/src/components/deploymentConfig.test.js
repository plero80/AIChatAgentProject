import assert from 'node:assert/strict'
import test from 'node:test'
import { loadDeploymentConfig } from './deploymentConfig.js'
import { sendChatMessage } from '../api.js'

test('loads branding from this deployment without a client-selected dietitian', async (t) => {
  const profile = { dietitian_id: 'clinic_002', brand_name: 'Dana', inspiration: [] }
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/public-config')
    assert.equal(options.cache, 'no-store')
    assert.equal(options.signal, controller.signal)
    return { ok: true, json: async () => profile }
  })
  assert.deepEqual(await loadDeploymentConfig(controller.signal), profile)
})

test('configuration failures do not silently use another dietitians branding', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 503 }))
  await assert.rejects(loadDeploymentConfig(), /Configuration unavailable/)
})

test('chat sends the message and host cookie, leaving tenant selection to the server', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/chat')
    assert.equal(options.credentials, 'include')
    assert.deepEqual(JSON.parse(options.body), { message: 'Hello!' })
    return { ok: true, json: async () => ({ reply: 'Hi' }) }
  })
  assert.deepEqual(await sendChatMessage('Hello!'), { reply: 'Hi' })
})
