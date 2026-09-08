import { useState } from 'react'

export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState('')

  function submit(event) {
    event.preventDefault()
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit(event)
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask Alona for a recipe, ingredients, or tips…"
        rows={2}
        disabled={disabled}
        aria-label="Message"
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        Send
      </button>
    </form>
  )
}

