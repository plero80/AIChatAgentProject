import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import recipeMarkdown from './recipeMarkdown.js'

const render = markdown => renderToStaticMarkup(createElement(ReactMarkdown, {
  remarkPlugins: [recipeMarkdown],
}, markdown))

const intro = 'בטח — הנה 2 מתכוני אלונה עם טופו:'
const followUp = 'אם תרצי, אני יכולה גם להציע לך איזה מתכון הכי מתאים לפי מה שיש לך בנוסף לטופו.'
const tofuSource = 'https://www.instagram.com/p/Db3SxwotrQg/?hl=en'
const rollsSource = 'https://www.instagram.com/p/DcN4D3AN1Ng/?hl=en'

function screenshotReply(ingredientLabel, sourceSeparator) {
  return `${intro}

## 1) טופו חמאת בוטנים

**${ingredientLabel}:**
- מים חמימים — 0.25 כוס
- טופו — 300 גרם
- סויה — 3 כפות
- חמאת בוטנים טבעית — 2 כפות

**הוראות הכנה:**
1. סופגים את הטופו היטב עם נייר סופג — זה עוזר לו להיות קריספי יותר.
2. חותכים את הטופו לקוביות ומעבירים אותו למחבת חמה.

**Instagram URL:**${sourceSeparator}${tofuSource}

---

## 2) ספרינג רולס קייציים ברוטב חמאת בוטנים

**${ingredientLabel}:**
- מייפל — 1 כף
- סויה — 3 כפות
- מים — 3 כפות
- מלפפון — לפי הצורך

**הוראות הכנה:**
1. טובלים דף אורז בקערת מים ומניחים על משטח נקי.
2. מכינים את הרוטב ומגישים.

**Instagram URL:**${sourceSeparator}${rollsSource}

${followUp}

![טופו חמאת בוטנים](/recipe-images/Db3SxwotrQg.jpg)

![ספרינג רולס ברוטב חמאת בוטנים](/recipe-images/DcN4D3AN1Ng.jpg)`
}

for (const ingredientLabel of ['המצרכים', 'המרכיבים', 'הרכיבים']) {
  for (const [sourceFormat, sourceSeparator] of [['line break', '\n'], ['separate paragraph', '\n\n']]) {
    test(`keeps screenshot recipe photos with their recipes: ${ingredientLabel}, source on ${sourceFormat}`, () => {
      const html = render(screenshotReply(ingredientLabel, sourceSeparator))
      const cards = [...html.matchAll(/<article class="recipe-card">[\s\S]*?<\/article>/g)].map(match => match[0])

      assert.equal(cards.length, 2)
      assert.match(cards[0], /1\) טופו חמאת בוטנים/)
      assert.match(cards[1], /2\) ספרינג רולס קייציים ברוטב חמאת בוטנים/)

      for (const [index, code, source] of [[0, 'Db3SxwotrQg', tofuSource], [1, 'DcN4D3AN1Ng', rollsSource]]) {
        const card = cards[index]
        const imageAt = card.indexOf(`src="/recipe-images/${code}.jpg"`)
        assert.ok(imageAt >= 0, `recipe ${index + 1} has its own photo`)
        assert.ok(card.indexOf('recipe-card-photos') < imageAt)
        assert.ok(imageAt < card.indexOf('recipe-ingredients'), 'photo precedes the ingredients')
        assert.equal((card.match(/<img /g) || []).length, 1)
        assert.ok(card.includes(`href="${source}"`), 'original source URL is preserved')
        assert.match(card, /class="recipe-card-source"/)
        assert.ok(!card.includes(followUp))
      }

      assert.match(cards[0], /מים חמימים — 0\.25 כוס/)
      assert.match(cards[0], /טופו — 300 גרם/)
      assert.match(cards[0], /סויה — 3 כפות/)
      assert.match(cards[0], /חותכים את הטופו לקוביות ומעבירים אותו למחבת חמה\./)
      assert.match(cards[1], /מייפל — 1 כף/)
      assert.match(cards[1], /מים — 3 כפות/)
      assert.match(cards[1], /טובלים דף אורז בקערת מים ומניחים על משטח נקי\./)
      assert.match(cards[1], /מכינים את הרוטב ומגישים\./)
      assert.equal((html.match(/<img /g) || []).length, 2)

      const outsideCards = html.replace(/<article class="recipe-card">[\s\S]*?<\/article>/g, '')
      assert.doesNotMatch(outsideCards, /<img /, 'no photos remain beneath the complete answer')
      assert.ok(html.indexOf(intro) < html.indexOf('<article'))
      assert.ok(html.indexOf(followUp) > html.lastIndexOf('</article>'))
      assert.equal((html.match(/class="recipe-card-source"/g) || []).length, 2)
    })
  }
}

test('does not interpret a generic bold sentence starting with an ingredient word as a recipe section', () => {
  const html = render(`## רעיונות לארוחת ערב

**המצרכים שיש בבית יכולים להספיק לארוחה טובה.**
- טופו
- ירקות

**הוראות הכנה:**
1. בוחרים יחד מה להכין.`)

  assert.doesNotMatch(html, /recipe-card|recipe-ingredients/)
  assert.match(html, /<strong>המצרכים שיש בבית יכולים להספיק לארוחה טובה\.<\/strong>/)
  assert.match(html, /בוחרים יחד מה להכין\./)
})

test('keeps qualified ingredient headings and serving counts recognizable', () => {
  for (const label of ['המצרכים לרוטב:', 'הרכיבים (ל-2 מנות)']) {
    const html = render(`## טופו\n\n**${label}**\n- טופו — 300 גרם\n\n**הוראות הכנה:**\n1. מערבבים ומגישים.`)
    assert.match(html, /class="recipe-card"/)
    assert.match(html, /class="recipe-ingredients"/)
    assert.match(html, /טופו — 300 גרם/)
  }
})
