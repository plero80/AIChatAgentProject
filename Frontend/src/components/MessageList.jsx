import { useLayoutEffect, useRef } from 'react'
import ChatMessage from './ChatMessage'
import RecipeContent from './RecipeContent'
import Icon from './Icon'

function UserMessageContent({ message }) {
  const images = message.images ?? (message.image ? [message.image] : [])
  return <>
    {images.length > 0 && <div className={`user-photos ${images.length === 1 ? 'single-photo' : ''}`}>
      {images.map((src, index) => <img key={index} className="user-photo" src={src} alt={`תמונת מצרכים ${index + 1} ששלחת`} />)}
    </div>}
    {message.content && <span>{message.content}</span>}
    {message.failed && <small className="failed-message">השליחה לא הושלמה. אפשר לנסות שוב.</small>}
  </>
}

export default function MessageList({ messages, isTyping, children, assistantName }) {
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
        <ChatMessage assistantName={assistantName} key={msg.id} role={msg.role} content={msg.role === 'assistant' ? <RecipeContent content={msg.content} blocks={msg.blocks} /> : <UserMessageContent message={msg} />} />
      ))}
      {isTyping && (
        <div className="typing" role="status">
          <span className="message-avatar"><Icon name="leaf" /></span>
          <span>{assistantName} חושבת</span>
          <span className="typing-dots" aria-hidden="true"><span /><span /><span /></span>
        </div>
      )}
    </div>
  )
}
