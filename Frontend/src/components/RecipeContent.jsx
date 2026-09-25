import ReactMarkdown from 'react-markdown'
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

export default function RecipeContent({ content }) {
  return (
    <div className="recipe-content" dir="auto">
      <ReactMarkdown remarkPlugins={plugins} components={components}>{content}</ReactMarkdown>
    </div>
  )
}
