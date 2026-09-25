import { useRef, useState } from 'react'
import { sendChatMessage } from './api'
import ChatInput from './components/ChatInput'
import MessageList from './components/MessageList'
import Icon from './components/Icon'
import tofuPhoto from '../../recipe_images/Db3SxwotrQg.jpg'
import rollsPhoto from '../../recipe_images/DcN4D3AN1Ng.jpg'
import './App.css'

const starters = [
  { icon: 'leaf', label: 'משהו עם טופו', message: 'יש לי טופו. איזה מתכון של אלונה אפשר להכין?' },
  { icon: 'bowl', label: 'ארוחה עשירה בחלבון', message: 'אני מחפשת מתכון לארוחה עשירה בחלבון' },
  { icon: 'sparkles', label: 'לבשל עם מה שיש', message: 'תעזרי לי למצוא מתכון לפי המצרכים שיש לי בבית' },
]

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

  async function handleSend(text) {
    if (sendingRef.current || !text.trim()) return
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

  return (
    <div className="shell" dir="rtl" lang="he">
      <header className="topbar">
        <div className="brand">
          <span className="brand-symbol"><Icon name="leaf" /></span>
          <div className="brand-lockup">
            <span className="brand-name" lang="en" dir="ltr">Alona<span>.</span></span>
            <span className="brand-caption">העוזרת שלך במטבח</span>
          </div>
        </div>
        <span className="topbar-note"><span /> קצת השראה, הרבה טעם</span>
      </header>

      <main className="stage">
        <aside className="kitchen-sidebar" aria-label="השראה למטבח">
          <div className="sidebar-intro">
            <span className="eyebrow"><span className="small-rule" /> מהמטבח של אלונה</span>
            <h1>משהו טעים<br />מתחיל <span>בשיחה.</span></h1>
            <p>מתכונים שאוהבים, מצרכים שכבר יש בבית וקצת השראה לארוחה הבאה.</p>
          </div>
          <div className="sidebar-recipes">
            <p className="sidebar-section-label">רעיון קטן להתחלה</p>
            <button className="inspiration-card" onClick={() => handleSend('אפשר את המתכון של אלונה לטופו חמאת בוטנים?')} disabled={isTyping}>
              <img src={tofuPhoto} alt="" />
              <span className="inspiration-text"><span>טופו חמאת בוטנים</span><small>למתכון של אלונה <Icon name="arrow" /></small></span>
            </button>
            <button className="inspiration-card" onClick={() => handleSend('אפשר את המתכון של אלונה לספרינג רולס ברוטב חמאת בוטנים?')} disabled={isTyping}>
              <img src={rollsPhoto} alt="" />
              <span className="inspiration-text"><span>ספרינג רולס</span><small>למתכון של אלונה <Icon name="arrow" /></small></span>
            </button>
          </div>
          <p className="sidebar-footer"><Icon name="bowl" /> מהמטבח, באהבה.</p>
        </aside>

        <section className="chat-panel" aria-label="צ׳אט עם אלונה">
          <header className="chat-header">
            <span className="assistant-avatar"><Icon name="leaf" /></span>
            <div><h2>המטבח של אלונה</h2><p>מוצאות יחד את הביס הבא</p></div>
            <span className="chat-header-tag">נעים לבשל יחד</span>
          </header>
          <MessageList messages={messages} isTyping={isTyping}>
            <div className="welcome">
              <div className="welcome-illustration" aria-hidden="true"><Icon name="bowl" /><span><Icon name="sparkles" /></span></div>
              <p className="eyebrow">ברוכה הבאה למטבח</p>
              <h2>מה מתחשק לך להכין?</h2>
              <p className="welcome-description">היי, אני העוזרת של אלונה.<br />ספרי לי מה יש במקרר, מה בא לך לאכול, או איזה מתכון את מחפשת.</p>
              <div className="starter-prompts">
                {starters.map((starter) => (
                  <button key={starter.label} onClick={() => handleSend(starter.message)} disabled={isTyping}>
                    <Icon name={starter.icon} /><span>{starter.label}</span><Icon name="arrow" />
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
      <footer className="page-footer">מתכונים מהמטבח, לא מהאוויר.<span lang="en" dir="ltr">ALONA’S KITCHEN</span></footer>
    </div>
  )
}
