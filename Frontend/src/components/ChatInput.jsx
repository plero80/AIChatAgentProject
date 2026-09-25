import { useLayoutEffect, useRef, useState } from 'react'
import Icon from './Icon'

export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState('')
  const inputRef = useRef(null)

  useLayoutEffect(() => {
    const input = inputRef.current
    if (!input) return
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 132)}px`
  }, [value])

  function submit(event) {
    event.preventDefault()
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
    inputRef.current?.focus()
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      submit(event)
    }
  }

  return (
    <form className="composer" onSubmit={submit} aria-label="כתיבת הודעה">
      <div className="composer-field">
        <textarea ref={inputRef} value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={onKeyDown} placeholder="מה יש לך במקרר? מה בא לך להכין?" rows={1} aria-label="הודעה לאלונה" aria-describedby="composer-hint" dir="auto" />
        <button type="submit" disabled={disabled || !value.trim()} aria-label="שליחת הודעה"><Icon name="send" /></button>
      </div>
      <div className="composer-footer" id="composer-hint"><span>קצת מצרכים, ונמצא יחד רעיון.</span><span className="keyboard-hint">Enter לשליחה <span>·</span> Shift + Enter לשורה חדשה</span></div>
    </form>
  )
}
