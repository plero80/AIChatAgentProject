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
        placeholder="שאלי את אלונה על מתכון, מצרכים או טיפים…"
        rows={2}
        disabled={disabled}
        aria-label="הודעה"
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        שלחי
      </button>
    </form>
  )
}

