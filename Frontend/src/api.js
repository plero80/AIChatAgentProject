export async function sendChatMessage(message) {
  const response = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ message }),
  })

  if (!response.ok) {
    let detail = ''
    try {
      detail = (await response.json()).detail
    } catch {
      detail = ''
    }
    throw new Error(detail || `Chat failed (${response.status})`)
  }

  return response.json()
}
