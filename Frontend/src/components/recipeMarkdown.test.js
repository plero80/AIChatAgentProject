import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import recipeMarkdown from './recipeMarkdown.js'

const render = markdown => renderToStaticMarkup(createElement(ReactMarkdown, {
  remarkPlugins: [recipeMarkdown],
}, markdown))

const recipe = (title, source = '') => `## ${title}

**רכיבים:**
- טופו — 300 גרם
- סויה — 3 כפות

**הוראות:**
1. חותכים את הטופו לקוביות.
2. מערבבים ומגישים.
${source ? `\n**Instagram:** ${source}\n` : ''}`

test('associates API tail photos by recipe name, preserving intro and follow-up prose', () => {
  const html = render(`הנה שני מתכונים:

${recipe('1) טופו חמאת בוטנים', 'https://www.instagram.com/p/tofu/?hl=en')}
---
${recipe('2) ספרינג רולס', 'https://www.instagram.com/p/rolls/?hl=en')}

אפשר להתאים את המתכונים למה שיש בבית.

![ספרינג רולס](/recipe-images/rolls.jpg)

![טופו חמאת בוטנים](/recipe-images/tofu.jpg)`)
  const cards = [...html.matchAll(/<article class="recipe-card">[\s\S]*?<\/article>/g)].map(match => match[0])
  assert.equal(cards.length, 2)
  assert.match(cards[0], /src="\/recipe-images\/tofu.jpg"/)
  assert.doesNotMatch(cards[0], /rolls.jpg/)
  assert.match(cards[1], /src="\/recipe-images\/rolls.jpg"/)
  assert.equal((html.match(/<img /g) || []).length, 2)
  assert.ok(cards[0].indexOf('tofu.jpg') < cards[0].indexOf('recipe-ingredients'))
  assert.ok(html.indexOf('הנה שני מתכונים') < html.indexOf('<article'))
  assert.ok(html.indexOf('אפשר להתאים') > html.lastIndexOf('</article>'))
  assert.match(cards[0], /טופו — 300 גרם/)
  assert.match(cards[0], /חותכים את הטופו לקוביות/)
  assert.doesNotMatch(html, /<hr/)
})

test('recognizes standalone bold recipe titles with ingredient and method headings', () => {
  const markdown = recipe('1) טופו').replace('## 1) טופו', '**1) טופו**')
  const html = render(markdown)
  assert.match(html, /<article class="recipe-card">/)
  assert.match(html, /class="recipe-ingredients"/)
  assert.match(html, /class="recipe-method"/)
})

test('preserves unrelated Markdown and does not style generic headings as recipes', () => {
  const html = render('## רעיונות\n\n**אפשרויות:**\n- שלום\n- עולם\n\n> ציטוט\n\n`code`')
  assert.doesNotMatch(html, /recipe-card/)
  assert.match(html, /<h2>רעיונות<\/h2>/)
  assert.match(html, /<blockquote>/)
  assert.match(html, /<code>code<\/code>/)
})

test('keeps ambiguous and unmatched tail photos outside recipe cards', () => {
  const html = render(`${recipe('1) טופו')}\n---\n${recipe('2) טופו')}\n\n![טופו](/same.jpg)\n\n![מנה אחרת](/unknown.jpg)`)
  assert.equal((html.match(/<article /g) || []).length, 2)
  assert.equal((html.match(/<img /g) || []).length, 2)
  assert.ok(html.indexOf('src="/same.jpg"') > html.lastIndexOf('</article>'))
  assert.ok(html.indexOf('src="/unknown.jpg"') > html.lastIndexOf('</article>'))
})

test('converts bare Instagram sources without exposing their long URL as link text', () => {
  const html = render('מקור: https://www.instagram.com/p/abc/?hl=en.\n\n[הסרטון המקורי](https://instagram.com/p/abc/)')
  assert.match(html, /href="https:\/\/www.instagram.com\/p\/abc\/\?hl=en"/)
  assert.match(html, />למתכון באינסטגרם<\/a>\./)
  assert.match(html, />הסרטון המקורי<\/a>/)
  assert.doesNotMatch(html, />https:\/\/www.instagram.com/)
})

test('does not autolink code or lookalike source domains', () => {
  const html = render('`https://instagram.com/p/code/`\n\nhttps://instagram.com.evil.example/p/fake/\n\n```txt\nhttps://instagram.com/p/fenced/\n```')
  assert.doesNotMatch(html, /<a /)
  assert.match(html, /<code>https:\/\/instagram.com\/p\/code\//)
})

test('retains ReactMarkdown protections for unsafe links and raw HTML', () => {
  const html = render(`${recipe('טופו')}\n\n[unsafe](javascript:alert%281%29)\n\n<script>alert(1)</script>`)
  assert.doesNotMatch(html, /href="javascript:/)
  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /&lt;script&gt;/)
})

test('supports English sections and uses an English source label', () => {
  const html = render('## Peanut tofu\n\n### Ingredients\n- Tofu\n\n### Instructions\n1. Cook the tofu.\n\nhttps://instagram.com/p/tofu/\n\n![Peanut tofu](/tofu.jpg)')
  assert.match(html, /<article class="recipe-card">/)
  assert.match(html, />View on Instagram<\/a>/)
  assert.match(html, /class="recipe-card-photos"/)
})

test('matches the saved Instagram post when the assistant changes the recipe title', () => {
  const html = render(`${recipe('1) ספרינג רולס קייציים ברוטב חמאת בוטנים', 'https://www.instagram.com/p/DcN4D3AN1Ng/?hl=en')}\n\n![ספרינג רולס ברוטב חמאת בוטנים](/recipe-images/DcN4D3AN1Ng.jpg?cache=1)`)
  const card = html.match(/<article class="recipe-card">[\s\S]*?<\/article>/)?.[0]
  assert.ok(card)
  assert.match(card, /class="recipe-card-photos"/)
  assert.match(card, /src="\/recipe-images\/DcN4D3AN1Ng.jpg\?cache=1"/)
  assert.equal((html.match(/<img /g) || []).length, 1)
})

test('does not move photos when source matching is ambiguous or conflicts with a title', () => {
  const source = 'https://instagram.com/p/shared/'
  const ambiguous = render(`${recipe('טופו', source)}\n\n${recipe('רולס', source)}\n\n![טופו](/recipe-images/shared.jpg)`)
  assert.ok(ambiguous.indexOf('src="/recipe-images/shared.jpg"') > ambiguous.lastIndexOf('</article>'))

  const conflicting = render(`${recipe('טופו', 'https://instagram.com/p/tofu/')}\n\n${recipe('רולס', 'https://instagram.com/p/rolls/')}\n\n![רולס](/recipe-images/tofu.jpg)`)
  assert.ok(conflicting.indexOf('src="/recipe-images/tofu.jpg"') > conflicting.lastIndexOf('</article>'))
})

test('recognizes a recipe with an existing header photo and moves it exactly once', () => {
  const markdown = recipe('טופו', 'https://instagram.com/p/tofu/')
    .replace('## טופו', '## טופו\n\n![טופו](/recipe-images/tofu.jpg)')
  const html = render(markdown)
  assert.match(html, /<article class="recipe-card">/)
  assert.match(html, /class="recipe-card-photos"/)
  assert.equal((html.match(/<img /g) || []).length, 1)
  assert.match(html, /טופו — 300 גרם/)
  assert.match(html, /מערבבים ומגישים/)
})

test('keeps unmatched inline photos and accompanying prose inside a recognizable recipe', () => {
  const markdown = recipe('טופו', 'https://instagram.com/p/tofu/')
    .replace('**רכיבים:**', '![מבט מקרוב](/closeup.jpg)\n\n**רכיבים:**')
    .replace('**Instagram:**', '![תהליך ההכנה](/process.jpg)\n\nבודקים שהטופו זהוב. ![הצעת הגשה](/serving.jpg)\n\n**Instagram:**')
  const html = render(markdown)
  const card = html.match(/<article class="recipe-card">[\s\S]*?<\/article>/)?.[0]
  assert.ok(card)
  assert.equal((card.match(/<img /g) || []).length, 3)
  assert.match(card, /src="\/closeup.jpg"/)
  assert.match(card, /src="\/process.jpg"/)
  assert.match(card, /בודקים שהטופו זהוב/)
  assert.match(card, /src="\/serving.jpg"/)
})
