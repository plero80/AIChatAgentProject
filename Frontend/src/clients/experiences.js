import michaelLipkin from './michaelLipkin.js'

const defaultExperience = Object.freeze({
  theme: 'alona',
  brandCaption: 'העוזרת שלך במטבח',
  topbarNote: 'קצת השראה, הרבה טעם',
  headline: ['משהו טעים', 'מתחיל ', 'בשיחה.'],
  introduction: 'מתכונים שאוהבים, מצרכים שכבר יש בבית וקצת השראה לארוחה הבאה.',
  sidebarFooter: 'מהמטבח, באהבה.',
  welcomeEyebrow: 'ברוכה הבאה למטבח',
  welcomeTitle: 'מה מתחשק לך להכין?',
  welcomeNote: 'כל ארוחה טובה מתחילה ברעיון קטן.',
  headerTag: 'נעים לבשל יחד',
  footerNote: 'מתכונים מהמטבח, לא מהאוויר.',
})

// Only reviewed local modules can run. The server never supplies JavaScript URLs.
export function experienceFor(config) {
  return config?.client_id === 'michael_lipkin' ? michaelLipkin : defaultExperience
}
