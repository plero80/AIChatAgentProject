import { useEffect, useRef, useState } from 'react'
import { sendChatMessage } from './api'
import { loadDeploymentConfig } from './components/deploymentConfig'
import ChatInput from './components/ChatInput'
import MessageList from './components/MessageList'
import Icon from './components/Icon'
import './App.css'


function hebrewError(message) {
  if (message?.includes('Too many') || message?.includes('429')) {
    return 'שלחנו הרבה הודעות. חכי רגע ואז נסי שוב.'
  }
  if (message?.includes('Service starting') || message?.includes('503')) {
    return 'המטבח עוד מתחמם. נסי שוב בעוד רגע.'
  }
  return 'לא הצלחתי להשיב כרגע. נסי שוב בעוד רגע.'
}

function newId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [isTyping, setIsTyping] = useState(false)
  const [error, setError] = useState(null)
  const sendingRef = useRef(false)
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    loadDeploymentConfig(controller.signal).then((profile) => {
      if (controller.signal.aborted) return
      setConfig(profile)
      document.title = profile.title
      document.querySelector('meta[name="description"]')?.setAttribute('content', profile.welcome)
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', profile.accent_color)
    }).catch(() => {
      if (!controller.signal.aborted) setConfigError(true)
    })
    return () => controller.abort()
  }, [])

  async function handleSend(text) {
    if (!config || sendingRef.current || !text.trim()) return
    sendingRef.current = true
    setError(null)
    setMessages((prev) => [...prev, { id: newId(), role: 'user', content: text }])
    setIsTyping(true)

    try {
      const data = await sendChatMessage(text)
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: 'assistant', content: data.reply, blocks: data.blocks },
      ])
    } catch (err) {
      setError(hebrewError(err.message))
    } finally {
      sendingRef.current = false
      setIsTyping(false)
    }
  }

  if (!config) {
    return <main className="config-status" dir="rtl" lang="he" role={configError ? 'alert' : 'status'}>
      <p>{configError ? 'לא הצלחנו לטעון את המטבח. נסי לרענן את העמוד.' : 'טוענים את המטבח…'}</p>
      {configError && <button onClick={() => window.location.reload()}>נסי שוב</button>}
    </main>
  }

  return (
    <div className="shell" dir="rtl" lang="he" style={{ '--olive': config.accent_color }}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-symbol"><Icon name="leaf" /></span>
          <div className="brand-lockup">
            <span className="brand-name" lang="en" dir="ltr">{config.brand_name}<span>.</span></span>
            <span className="brand-caption">העוזרת שלך במטבח</span>
          </div>
        </div>
        <span className="topbar-note"><span /> קצת השראה, הרבה טעם</span>
      </header>

      <main className="stage">
        <aside className="kitchen-sidebar" aria-label="השראה למטבח">
          <div className="sidebar-intro">
            <span className="eyebrow"><span className="small-rule" /> {config.title}</span>
            <h1>משהו טעים<br />מתחיל <span>בשיחה.</span></h1>
            <p>מתכונים שאוהבים, מצרכים שכבר יש בבית וקצת השראה לארוחה הבאה.</p>
          </div>
          {config.inspiration.length > 0 && <div className="sidebar-recipes">
            <p className="sidebar-section-label">רעיון קטן להתחלה</p>
            {config.inspiration.map((recipe) => (
              <button key={recipe.image} className="inspiration-card" onClick={() => handleSend(recipe.message)} disabled={isTyping}>
                <img src={`/recipe-images/${recipe.image}`} alt="" />
                <span className="inspiration-text"><span>{recipe.label}</span><small>למתכון <Icon name="arrow" /></small></span>
              </button>
            ))}
          </div>}
          <p className="sidebar-footer"><Icon name="bowl" /> מהמטבח, באהבה.</p>
        </aside>

        <section className="chat-panel" aria-label={config.title}>
          <header className="chat-header">
            <span className="assistant-avatar"><Icon name="leaf" /></span>
            <div><h2>{config.title}</h2><p>{config.subtitle}</p></div>
            <span className="chat-header-tag">נעים לבשל יחד</span>
          </header>
          <MessageList messages={messages} isTyping={isTyping} assistantName={config.assistant_name}>
            <div className="welcome">
              <div className="welcome-illustration" aria-hidden="true"><Icon name="bowl" /><span><Icon name="sparkles" /></span></div>
              <p className="eyebrow">ברוכה הבאה למטבח</p>
              <h2>מה מתחשק לך להכין?</h2>
              <p className="welcome-description">{config.welcome}</p>
              <div className="starter-prompts">
                {config.starters.map((starter) => (
                  <button key={starter.label} onClick={() => handleSend(starter.message)} disabled={isTyping}>
                    <Icon name="leaf" /><span>{starter.label}</span><Icon name="arrow" />
                  </button>
                ))}
              </div>
              <p className="welcome-note">כל ארוחה טובה מתחילה ברעיון קטן.</p>
            </div>
          </MessageList>
          {error && <p className="error" role="alert">{error}</p>}
          <ChatInput onSend={handleSend} disabled={isTyping} />
        </section>
      </main>
      <footer className="page-footer">מתכונים מהמטבח, לא מהאוויר.<span lang="en" dir="ltr">{config.footer}</span></footer>
    </div>
  )
}
