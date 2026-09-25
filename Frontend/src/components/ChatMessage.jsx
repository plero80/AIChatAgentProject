import Icon from './Icon'

export default function ChatMessage({ role, content }) {
  const isUser = role === 'user'
  return (
    <article data-message className={`bubble ${isUser ? 'bubble-user' : 'bubble-alona'}`}>
      <div className="bubble-label">{!isUser && <span className="message-avatar"><Icon name="leaf" /></span>}<span>{isUser ? 'את' : 'אלונה'}</span></div>
      <div className="bubble-body" dir="auto">{content}</div>
    </article>
  )
}
