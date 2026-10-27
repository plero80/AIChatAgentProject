import assert from 'node:assert/strict'
import test from 'node:test'
import { clientApiPath, clientSlug } from '../clientRouting.js'
import { sendChatMessage } from '../api.js'
import { loadDeploymentConfig } from './deploymentConfig.js'

test('client URL maps to matching API paths and rejects ambiguous paths', () => {
  assert.equal(clientSlug('/c/demo-chef'), 'demo-chef')
  assert.equal(clientSlug('/c/demo-chef/'), 'demo-chef')
  assert.equal(clientSlug('/'), null)
  assert.equal(clientApiPath('chat', 'demo-chef'), '/api/clients/demo-chef/chat')
  for (const path of ['/c/../chef', '/c/chef/other', '/unknown', '/c/%2e%2e', '/c/']) {
    assert.throws(() => clientSlug(path), /Unknown client URL/)
  }
})

test('config and chat use the same client with cookies and cancellation', async (t) => {
  const seen = []
  const controller = new AbortController()
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    seen.push(url)
    assert.equal(options.credentials, 'include')
    assert.equal(options.signal, controller.signal)
    if (options.body) assert.deepEqual(JSON.parse(options.body), { message: 'Dinner' })
    return { ok: true, json: async () => ({ reply: 'Done' }) }
  })
  await loadDeploymentConfig(controller.signal, 'chef')
  await sendChatMessage('Dinner', { slug: 'chef', signal: controller.signal })
  assert.deepEqual(seen, ['/api/clients/chef/public-config', '/api/clients/chef/chat'])
})
