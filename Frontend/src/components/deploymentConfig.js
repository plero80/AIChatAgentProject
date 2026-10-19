export async function loadDeploymentConfig(signal) {
  const response = await fetch('/public-config', { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Configuration unavailable (${response.status})`)
  return response.json()
}
