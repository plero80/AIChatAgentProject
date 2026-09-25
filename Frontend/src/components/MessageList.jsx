import { useLayoutEffect, useRef } from 'react'
import ChatMessage from './ChatMessage'
import RecipeContent from './RecipeContent'
import Icon from './Icon'

export default function MessageList({ messages, isTyping, children }) {
  const listRef = useRef(null)

  useLayoutEffect(() => {
    const list = listRef.current
    const lastMessage = list?.querySelector('[data-message]:last-of-type')
    if (!list || !lastMessage) return

    // Show the start of long answers without scrolling the page or composer.
    if (messages.at(-1)?.role === 'assistant') {
      list.scrollTop += lastMessage.getBoundingClientRect().top - list.getBoundingClientRect().top - 24
    } else {
      list.scrollTop = list.scrollHeight
    }
  }, [messages, isTyping])

  return (
    <div className={`message-list ${messages.length ? '' : 'message-list-empty'}`} ref={listRef} role="log" aria-label="הודעות השיחה" aria-live="polite" aria-relevant="additions" tabIndex={0}>
      {messages.length === 0 ? children : <p className="conversation-start">משהו טוב מתבשל כאן</p>}
      {messages.map((msg) => (
        <ChatMessage key={msg.id} role={msg.role} content={msg.role === 'assistant' ? <RecipeContent content={msg.content} /> : msg.content} />
      ))}
      {isTyping && (
        <div className="typing" role="status">
          <span className="message-avatar"><Icon name="leaf" /></span>
          <span>אלונה חושבת</span>
          <span className="typing-dots" aria-hidden="true"><span /><span /><span /></span>
        </div>
      )}
    </div>
  )
}
