import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// Load the actual JSX renderer through the app's existing compiler.
const server = await createServer({ server: { middlewareMode: true, watch: null }, logLevel: 'silent' })
after(() => server.close())
const { default: RecipeContent } = await server.ssrLoadModule('/src/components/RecipeContent.jsx')
const render = props => renderToStaticMarkup(createElement(RecipeContent, props))
const cardsIn = html => [...html.matchAll(/<article\b[\s\S]*?<\/article>/g)].map(match => match[0])
const recipe = overrides => ({
  id: 'tofu', name: 'טופו חמאת בוטנים', description: 'מנה פשוטה לארוחת ערב.',
  ingredients: [
    { ingredient_name: 'מים', quantity: '0.25', unit: 'כוס' },
    { ingredient_name: 'מלח', quantity: 0, unit: 'גרם' },
    { ingredient_name: 'בצל ירוק', quantity: null, unit: null },
  ],
  instructions: '1. חותכים את הטופו.\n2. מערבבים ומגישים.',
  instagram_url: 'https://www.instagram.com/p/tofu/',
  image_url: '/recipe-images/tofu.jpg',
  ...overrides,
})

test('structured recipe photo stays with canonical title and body despite prose mentioning another recipe', () => {
  const html = render({
    content: 'Unused legacy reply ![extra](/recipe-images/rolls.jpg)',
    blocks: [
      { type: 'text', text: 'הנה המתכון. אפשר גם לבקש ספרינג רולס בהמשך.' },
      { type: 'recipe', recipe: recipe() },
    ],
  })
  const cards = cardsIn(html)
  assert.equal(cards.length, 1)
  assert.equal((html.match(/<img /g) ?? []).length, 1)
  assert.match(cards[0], /data-recipe-id="tofu"/)
  assert.match(cards[0], /class="recipe-card-title">טופו חמאת בוטנים/)
  assert.match(cards[0], /alt="טופו חמאת בוטנים"/)
  assert.match(cards[0], /חותכים את הטופו/)
  assert.ok(cards[0].indexOf('recipe-card-title') < cards[0].indexOf('tofu.jpg'))
  assert.ok(cards[0].indexOf('tofu.jpg') < cards[0].indexOf('recipe-ingredients'))
  assert.doesNotMatch(html, /Unused legacy|rolls\.jpg/)
})

test('renders multiple recipes in explicit block order with only their own photos', () => {
  const html = render({ blocks: [
    { type: 'text', text: 'תחילת התשובה' },
    { type: 'recipe', recipe: recipe({ id: 'rolls', name: 'ספרינג רולס', image_url: '/recipe-images/rolls.jpg' }) },
    { type: 'text', text: 'והאפשרות השנייה' },
    { type: 'recipe', recipe: recipe() },
    { type: 'text', text: 'בתיאבון' },
  ] })
  const cards = cardsIn(html)
  assert.equal(cards.length, 2)
  assert.match(cards[0], /rolls\.jpg/)
  assert.doesNotMatch(cards[0], /tofu\.jpg/)
  assert.match(cards[1], /tofu\.jpg/)
  assert.doesNotMatch(cards[1], /rolls\.jpg/)
  assert.ok(html.indexOf('תחילת התשובה') < html.indexOf('<article'))
  assert.ok(html.indexOf('והאפשרות השנייה') > html.indexOf('</article>'))
  assert.ok(html.indexOf('והאפשרות השנייה') < html.lastIndexOf('<article'))
  assert.ok(html.indexOf('בתיאבון') > html.lastIndexOf('</article>'))
})

test('explicit empty blocks override reply while absent blocks preserve legacy rendering', () => {
  const content = '## Legacy\n\n![Legacy photo](/old.jpg)'
  assert.doesNotMatch(render({ content, blocks: [] }), /Legacy|<img /)
  assert.match(render({ content }), /<h2>Legacy<\/h2>/)
  assert.match(render({ content }), /src="\/old.jpg"/)
})

test('text blocks preserve safe Markdown but cannot introduce detached photos or HTML', () => {
  const html = render({ blocks: [{ type: 'text', text: [
    '## A heading', '**Some advice**', '![Detached image](/extra.jpg)',
    '[Unsafe](javascript:alert%281%29)', '<script>alert(1)</script>',
    '[Safe](https://example.com/advice)',
  ].join('\n\n') }] })
  assert.match(html, /<h2>A heading<\/h2>/)
  assert.match(html, /<strong>Some advice<\/strong>/)
  assert.match(html, /href="https:\/\/example.com\/advice"/)
  assert.doesNotMatch(html, /<img |<script>|href="javascript:/)
})

test('missing and unsafe images retain the full recipe context', () => {
  for (const image_url of [null, '', 'javascript:alert(1)', 'data:image/svg+xml,<svg/>', '\\evil.example\\photo.jpg']) {
    const html = render({ blocks: [{ type: 'recipe', recipe: recipe({ image_url }) }] })
    assert.equal(cardsIn(html).length, 1)
    assert.doesNotMatch(html, /<img |recipe-card-photos/)
    assert.match(html, /טופו חמאת בוטנים/)
    assert.match(html, /חותכים את הטופו/)
    assert.match(html, /למתכון באינסטגרם/)
  }
})

test('canonical amounts preserve decimals, zero, and unspecified quantities without invented text', () => {
  const html = render({ blocks: [{ type: 'recipe', recipe: recipe() }] })
  assert.match(html, /מים — <bdi>0\.25 כוס<\/bdi>/)
  assert.match(html, /מלח — <bdi>0 גרם<\/bdi>/)
  assert.match(html, /<li>בצל ירוק<\/li>/)
  assert.doesNotMatch(html, /ללא כמות|לפי הצורך/)
})

test('canonical sources reject unsafe schemes and ingredient names stay escaped', () => {
  const html = render({ blocks: [{ type: 'recipe', recipe: recipe({
    instagram_url: 'javascript:alert(1)',
    ingredients: [{ ingredient_name: '<img src=x onerror=alert(1)>', quantity: null, unit: null }],
  }) }] })
  assert.doesNotMatch(html, /recipe-card-source|href="javascript:|<img src="x"/)
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/)
})

test('English canonical recipes use English labels and direction without heading detection', () => {
  const html = render({ blocks: [{ type: 'recipe', recipe: recipe({
    name: 'Peanut tofu', description: null,
    ingredients: [{ ingredient_name: 'Tofu', quantity: 300, unit: 'g' }],
    instructions: 'Cook the tofu.\nServe with rice.',
  }) }] })
  assert.match(html, /dir="ltr" lang="en"/)
  assert.match(html, />Ingredients<\/h4>/)
  assert.match(html, />Instructions<\/h4>/)
  assert.match(html, /View on Instagram/)
  assert.match(html, /Cook the tofu\.\nServe with rice\./)
})
