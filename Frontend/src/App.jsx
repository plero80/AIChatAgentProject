import { useEffect, useRef, useState } from 'react'
import { sendChatMessage } from './api'
import { loadDeploymentConfig } from './components/deploymentConfig'
import { clientSlug } from './clientRouting'
import ChatInput from './components/ChatInput'
import MessageList from './components/MessageList'
import Icon from './components/Icon'
import { experienceFor } from './clients/experiences'
import './App.css'
import './clients/michaelLipkin.css'


function hebrewError(message) {
  if (/at most 10 photos/i.test(message)) {
    return 'אפשר לצרף עד 10 תמונות לכל הודעה.'
  }
  if (/recognition is unavailable/i.test(message)) {
    return 'לא הצלחנו לזהות את המצרכים בתמונה כרגע. נסי שוב או כתבי מה יש בתמונה.'
  }
  if (/photo|image|megapixels|still photo/i.test(message)) {
    return 'לא הצלחנו לקבל את התמונה. בחרי תמונת JPEG, PNG או WebP, עד 5 MB ו־20 מגה־פיקסל.'
  }
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
  const [path, setPath] = useState(window.location.pathname)
  useEffect(() => {
    const changed = () => setPath(window.location.pathname)
    window.addEventListener('popstate', changed)
    return () => window.removeEventListener('popstate', changed)
  }, [])
  let slug
  try {
    slug = clientSlug(path)
  } catch {
    return <main role="alert">Unknown client URL</main>
  }
  return <ClientChat key={path} slug={slug} />
}

function ClientChat({ slug }) {
  const [messages, setMessages] = useState([])
  const [isTyping, setIsTyping] = useState(false)
  const [error, setError] = useState(null)
  const sendingRef = useRef(false)
  const sendController = useRef(null)
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState(false)

  useEffect(() => {
    if (!config) return
    const experience = experienceFor(config)
    document.documentElement.dataset.theme = experience.theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', experience.themeColor || config.accent_color)
    return () => { delete document.documentElement.dataset.theme }
  }, [config])

  useEffect(() => {
    const controller = new AbortController()
    loadDeploymentConfig(controller.signal, slug).then((profile) => {
      if (controller.signal.aborted) return
      setConfig(profile)
      document.title = profile.title
      document.querySelector('meta[name="description"]')?.setAttribute('content', profile.welcome)
    }).catch(() => {
      if (!controller.signal.aborted) setConfigError(true)
    })
    return () => {
      controller.abort()
      sendController.current?.abort()
    }
  }, [slug])

  async function handleSend(text, photos = []) {
    if (!config || sendingRef.current || (!text.trim() && !photos.length)) return false
    sendingRef.current = true
    const controller = new AbortController()
    sendController.current = controller
    setError(null)
    const messageId = newId()
    setMessages((prev) => [...prev, { id: messageId, role: 'user', content: text, images: photos.map((photo) => photo.preview) }])
    setIsTyping(true)

    try {
      const data = await sendChatMessage(text, { slug, signal: controller.signal, images: photos })
      if (controller.signal.aborted) return false
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: 'assistant', content: data.reply, blocks: data.blocks },
      ])
      return true
    } catch (err) {
      if (controller.signal.aborted) return false
      setMessages((prev) => prev.map((message) => message.id === messageId ? { ...message, failed: true } : message))
      setError(hebrewError(err.message))
      return false
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

  const experience = experienceFor(config)
  return (
    <div className="shell" data-theme={experience.theme} dir="rtl" lang="he" style={{ '--olive': config.accent_color }}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-symbol"><Icon name="leaf" /></span>
          <div className="brand-lockup">
            <span className="brand-name" lang="en" dir="ltr">{config.brand_name}<span>.</span></span>
            <span className="brand-caption">{experience.brandCaption}</span>
          </div>
        </div>
        <span className="topbar-note"><span /> {experience.topbarNote}</span>
      </header>

      <main className="stage">
        <aside className="kitchen-sidebar" aria-label="השראה למטבח">
          <div className="sidebar-intro">
            <span className="eyebrow"><span className="small-rule" /> {config.title}</span>
            <h1>{experience.headline[0]}<br />{experience.headline[1]}<span>{experience.headline[2]}</span></h1>
            <p>{experience.introduction}</p>
          </div>
          {config.inspiration.length > 0 && <div className="sidebar-recipes">
            <p className="sidebar-section-label">רעיון קטן להתחלה</p>
            {config.inspiration.map((recipe) => (
              <button key={recipe.image} className="inspiration-card" onClick={() => handleSend(recipe.message)} disabled={isTyping}>
                <img src={`${config.image_base_url || '/recipe-images'}/${recipe.image}`} alt="" />
                <span className="inspiration-text"><span>{recipe.label}</span><small>למתכון <Icon name="arrow" /></small></span>
              </button>
            ))}
          </div>}
          <p className="sidebar-footer"><Icon name="bowl" /> {experience.sidebarFooter}</p>
        </aside>

        <section className="chat-panel" aria-label={config.title}>
          <header className="chat-header">
            <span className="assistant-avatar"><Icon name="leaf" /></span>
            <div><h2>{config.title}</h2><p>{config.subtitle}</p></div>
            <span className="chat-header-tag">{experience.headerTag}</span>
          </header>
          <MessageList messages={messages} isTyping={isTyping} assistantName={config.assistant_name}>
            <div className="welcome">
              <div className="welcome-illustration" aria-hidden="true"><Icon name="bowl" /><span><Icon name="sparkles" /></span></div>
              <p className="eyebrow">{experience.welcomeEyebrow}</p>
              <h2>{experience.welcomeTitle}</h2>
              <p className="welcome-description">{config.welcome}</p>
              <div className="starter-prompts">
                {config.starters.map((starter) => (
                  <button key={starter.label} onClick={() => handleSend(starter.message)} disabled={isTyping}>
                    <Icon name="leaf" /><span>{starter.label}</span><Icon name="arrow" />
                  </button>
                ))}
              </div>
              <p className="welcome-note">{experience.welcomeNote}</p>
            </div>
          </MessageList>
          {error && <p className="error" role="alert">{error}</p>}
          <ChatInput onSend={handleSend} disabled={isTyping} imageUpload={config.image_upload} />
        </section>
      </main>
      <footer className="page-footer">{experience.footerNote}<span lang="en" dir="ltr">{config.footer}</span></footer>
    </div>
  )
}
