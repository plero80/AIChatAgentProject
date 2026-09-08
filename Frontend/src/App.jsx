import { useState } from 'react'
import { sendChatMessage } from './api'
import ChatInput from './components/ChatInput'
import MessageList from './components/MessageList'
import './App.css'

function hebrewError(message) {
  if (!message) return 'משהו השתבש. נסי שוב.'
  if (message.includes('Too many') || message.includes('429')) {
    return 'יותר מדי הודעות. רגע, ואז נסי שוב.'
  }
  if (message.includes('Failed to generate')) {
    return 'לא הצלחתי להשיב. נסי שוב בעוד רגע.'
  }
  return message
}

function newId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [messages, setMessages] = useState([
    {
      id: 'welcome',
      role: 'assistant',
      content: 'שלום, אני העוזרת של אלונה, אני כאן לכל בקשה 😊',
    },
  ])
  const [isTyping, setIsTyping] = useState(false)
  const [error, setError] = useState(null)

  async function handleSend(text) {
    setError(null)
    setMessages((prev) => [...prev, { id: newId(), role: 'user', content: text }])
    setIsTyping(true)

    try {
      const data = await sendChatMessage(text)
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: 'assistant', content: data.reply },
      ])
    } catch (err) {
      setError(hebrewError(err.message))
    } finally {
      setIsTyping(false)
    }
  }

  return (
    <div className="shell">
      <div className="glow glow-a" aria-hidden="true" />
      <div className="glow glow-b" aria-hidden="true" />

      <main className="stage">
        <header className="brand" lang="he" dir="rtl">
          <p className="brand-name" lang="en" dir="ltr">Alona</p>
          <h1 className="brand-line">מתכונים מהמטבח, לא מהאוויר.</h1>
          <p className="brand-sub">
            שאלי על מתכונים שמורים מאינסטגרם, התאמת מצרכים, או עזרה בבישול.
          </p>
        </header>

        <section className="chat-panel" dir="rtl" lang="he" aria-label="צ׳אט עם אלונה">
          <MessageList messages={messages} isTyping={isTyping} />
          {error && <p className="error">{error}</p>}
          <ChatInput onSend={handleSend} disabled={isTyping} />
        </section>
      </main>
    </div>
  )
}

