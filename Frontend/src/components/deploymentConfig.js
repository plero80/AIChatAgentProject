import { clientApiPath, clientSlug } from '../clientRouting.js'

export async function loadDeploymentConfig(signal, slug = clientSlug()) {
  const response = await fetch(clientApiPath('public-config', slug), { signal, cache: 'no-store', credentials: 'include' })
  if (!response.ok) throw new Error(`Configuration unavailable (${response.status})`)
  return response.json()
}
