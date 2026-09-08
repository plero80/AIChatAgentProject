import { useState } from 'react'
import { sendChatMessage } from './api'
import ChatInput from './components/ChatInput'
import MessageList from './components/MessageList'
import './App.css'

function newId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [messages, setMessages] = useState([])
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
      setError(err.message || 'Something went wrong')
    } finally {
      setIsTyping(false)
    }
  }

  return (
    <div className="shell">
      <div className="glow glow-a" aria-hidden="true" />
      <div className="glow glow-b" aria-hidden="true" />

      <main className="stage">
        <header className="brand">
          <p className="brand-name">Alona</p>
          <h1 className="brand-line">Recipes from the kitchen, not from thin air.</h1>
          <p className="brand-sub">
            Ask for saved Instagram recipes, ingredient matches, or cooking help.
          </p>
        </header>

        <section className="chat-panel" aria-label="Chat with Alona">
          <MessageList messages={messages} isTyping={isTyping} />
          {error && <p className="error">{error}</p>}
          <ChatInput onSend={handleSend} disabled={isTyping} />
        </section>
      </main>
    </div>
  )
}

