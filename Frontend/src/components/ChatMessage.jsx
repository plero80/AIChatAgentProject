export default function ChatMessage({ role, content }) {
  const isUser = role === 'user'

  return (
    <article className={`bubble ${isUser ? 'bubble-user' : 'bubble-alona'}`}>
      <span className="bubble-label">{isUser ? 'את' : 'אלונה'}</span>
      <div className="bubble-body">{content}</div>
    </article>
  )
}

