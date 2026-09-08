import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import ChatMessage from './ChatMessage'

export default function MessageList({ messages, isTyping }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  return (
    <div className="message-list" role="log" aria-live="polite">
      {messages.map((msg) => (
        <ChatMessage
          key={msg.id}
          role={msg.role}
          content={
            msg.role === 'assistant' ? (
              <ReactMarkdown>{msg.content}</ReactMarkdown>
            ) : (
              msg.content
            )
          }
        />
      ))}

      {isTyping && (
        <article className="bubble bubble-alona typing">
          <span className="bubble-label">אלונה</span>
          <div className="bubble-body">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </div>
        </article>
      )}

      <div ref={endRef} />
    </div>
  )
}

