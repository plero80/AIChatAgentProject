export default function ChatMessage({ role, content }) {
  const isUser = role === 'user'

  return (
    <article className={`bubble ${isUser ? 'bubble-user' : 'bubble-alona'}`}>
      <span className="bubble-label">{isUser ? 'You' : 'Alona'}</span>
      <div className="bubble-body">{content}</div>
    </article>
  )
}

