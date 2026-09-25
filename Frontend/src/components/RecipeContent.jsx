import { useState } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import recipeMarkdown, { isInstagramUrl } from './recipeMarkdown'
import './RecipeContent.css'

function MarkdownLink({ href, children, title, className }) {
  const isSource = isInstagramUrl(href)

  return (
    <a
      href={href}
      title={title}
      className={className}
      target={/^https?:/i.test(href ?? '') ? '_blank' : undefined}
      rel={/^https?:/i.test(href ?? '') ? 'noopener noreferrer' : undefined}
    >
      {isSource && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="5" />
          <circle cx="12" cy="12" r="4" />
          <circle cx="17.5" cy="6.5" r=".8" fill="currentColor" stroke="none" />
        </svg>
      )}
      {children}
      {isSource && <span aria-hidden="true" className="recipe-source-arrow">↗</span>}
    </a>
  )
}

function RecipeImage({ src, alt, title }) {
  return <img src={src} alt={alt ?? ''} title={title} loading="lazy" decoding="async" />
}

const plugins = [recipeMarkdown]
const components = { a: MarkdownLink, img: RecipeImage }
const textComponents = { a: MarkdownLink, img: () => null }

function safeRecipeUrl(value) {
  if (typeof value !== 'string') return null
  const url = value.trim()
  if (!url || defaultUrlTransform(url) !== url) return null
  if ([...url].some(character => character.charCodeAt(0) <= 32 || character === '\\')) return null
  try {
    return /^https?:$/i.test(new URL(url, 'https://recipe.local').protocol) ? url : null
  } catch {
    return null
  }
}

function RecipePhoto({ src, name }) {
  const [failedSrc, setFailedSrc] = useState(null)
  if (!src || failedSrc === src) return null

  return (
    <div className="recipe-card-photos">
      <img src={src} alt={name} loading="lazy" decoding="async" onError={() => setFailedSrc(src)} />
    </div>
  )
}

function ingredientAmount({ quantity, unit }) {
  return [quantity, unit]
    .filter(value => value !== null && value !== undefined && String(value).trim() !== '')
    .join(' ')
}

function RecipeCard({ recipe }) {
  const ingredients = recipe.ingredients ?? []
  const isHebrew = /[\u0590-\u05ff]/u.test([
    recipe.name, recipe.description, recipe.instructions,
    ...ingredients.map(ingredient => ingredient.ingredient_name),
  ].join(' '))
  const source = safeRecipeUrl(recipe.instagram_url)
  const sourceLabel = isInstagramUrl(source)
    ? (isHebrew ? 'למתכון באינסטגרם' : 'View on Instagram')
    : (isHebrew ? 'למתכון המקורי' : 'View original recipe')

  return (
    <article className="recipe-card" data-recipe-id={recipe.id} dir={isHebrew ? 'rtl' : 'ltr'} lang={isHebrew ? 'he' : 'en'}>
      <header className="recipe-card-heading">
        <h3 className="recipe-card-title">{recipe.name}</h3>
        {recipe.description && <p className="recipe-card-description">{recipe.description}</p>}
      </header>
      <RecipePhoto src={safeRecipeUrl(recipe.image_url)} name={recipe.name} />
      <div className="recipe-card-body">
        {ingredients.length > 0 && (
          <section className="recipe-ingredients">
            <h4 className="recipe-section-heading">{isHebrew ? 'המצרכים' : 'Ingredients'}</h4>
            <ul>
              {ingredients.map((ingredient, index) => {
                const amount = ingredientAmount(ingredient)
                return (
                  <li key={index}>
                    {ingredient.ingredient_name}{amount && <> — <bdi>{amount}</bdi></>}
                  </li>
                )
              })}
            </ul>
          </section>
        )}
        {recipe.instructions && (
          <section className="recipe-method">
            <h4 className="recipe-section-heading">{isHebrew ? 'הוראות הכנה' : 'Instructions'}</h4>
            <ReactMarkdown components={textComponents}>{recipe.instructions}</ReactMarkdown>
          </section>
        )}
        {source && (
          <footer className="recipe-card-source">
            <MarkdownLink href={source} className="recipe-source-link">{sourceLabel}</MarkdownLink>
          </footer>
        )}
      </div>
    </article>
  )
}

export default function RecipeContent({ content, blocks }) {
  return (
    <div className="recipe-content" dir="auto">
      {Array.isArray(blocks) ? blocks.map((block, index) => {
        if (block.type === 'recipe' && block.recipe) {
          return <RecipeCard key={`${block.recipe.id}-${index}`} recipe={block.recipe} />
        }
        if (block.type === 'text') {
          return (
            <div className="recipe-text-block" dir="auto" key={index}>
              <ReactMarkdown components={textComponents}>{block.text}</ReactMarkdown>
            </div>
          )
        }
        return null
      }) : <ReactMarkdown remarkPlugins={plugins} components={components}>{content}</ReactMarkdown>}
    </div>
  )
}
