// Work on Markdown's parsed tree: prose, code, links, and HTML retain the
// escaping and URL protections provided by ReactMarkdown.
const ingredientLabel = /^(?:מצרכים|מרכיבים|רכיבים|ingredients)(?:\s|:|$)/iu
const methodLabel = /^(?:אופן\s+(?:ההכנה|הכנה)|הוראות(?:\s+הכנה)?|הכנה|instructions|directions|method|preparation)(?:\s|:|$)/iu
const otherLabel = /^(?:טיפים?|הערות|הגשה|בתיאבון|instagram|אינסטגרם|מקור|tips?|notes?|serving|source)(?:\s|:|$)/iu

function textOf(node) {
  return node.value ?? node.children?.map(textOf).join('') ?? ''
}

function normalizeTitle(value) {
  return value.normalize('NFKC').toLowerCase()
    .replace(/^\s*\d+\s*[.)\-:]\s*/u, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
}

function sectionKind(node) {
  if (node.type !== 'heading' && node.type !== 'paragraph') return null
  const label = textOf(node).trim().replace(/[：:]+$/, '')
  // A section label is short and contains no prose or list items.
  if (label.length > 55 || node.children?.some(child => child.type === 'image' || child.type === 'link')) return null
  if (ingredientLabel.test(label)) return 'ingredients'
  if (methodLabel.test(label)) return 'method'
  return null
}

function recipeTitle(node) {
  if (sectionKind(node)) return null
  const text = textOf(node).trim()
  if (!text || text.length > 160 || otherLabel.test(text)) return null
  if (node.type === 'heading' && node.depth <= 3) return text
  if (node.type === 'paragraph') {
    const children = node.children.filter(child => child.type !== 'text' || child.value.trim())
    if (children.length === 1 && children[0].type === 'strong') return text
  }
  return null
}

export function isInstagramUrl(value) {
  try {
    const url = new URL(value)
    return /^https?:$/.test(url.protocol) && /^(?:www\.)?instagram\.com$/i.test(url.hostname)
  } catch {
    return false
  }
}

function sourceLink(url, label) {
  return {
    type: 'link', url,
    children: [{ type: 'text', value: label }],
    data: { hProperties: { className: 'recipe-source-link' } },
  }
}

function formatInstagramLinks(node, label) {
  if (node.type === 'link') {
    if (isInstagramUrl(node.url)) {
      node.data = { ...node.data, hProperties: { ...node.data?.hProperties, className: 'recipe-source-link' } }
      // Keep meaningful link labels, replacing only a visible URL.
      if (isInstagramUrl(textOf(node).trim())) node.children = [{ type: 'text', value: label }]
    }
    return
  }
  if (!node.children || node.type === 'linkReference') return
  node.children = node.children.flatMap(child => {
    if (child.type !== 'text') {
      formatInstagramLinks(child, label)
      return [child]
    }
    const pattern = /https?:\/\/(?:www\.)?instagram\.com\/[^\s<>(){}[\]]+/gi
    const result = []
    let cursor = 0
    for (const match of child.value.matchAll(pattern)) {
      const url = match[0].replace(/[.,;:!?]+$/, '')
      if (match.index > cursor) result.push({ type: 'text', value: child.value.slice(cursor, match.index) })
      result.push(sourceLink(url, label))
      cursor = match.index + url.length
    }
    if (!result.length) return [child]
    if (cursor < child.value.length) result.push({ type: 'text', value: child.value.slice(cursor) })
    return result
  })
}

function isSourceParagraph(node) {
  if (node.type !== 'paragraph') return false
  const links = node.children.filter(child => child.type === 'link')
  if (links.length !== 1 || !isInstagramUrl(links[0].url)) return false
  const rest = node.children.filter(child => child !== links[0]).map(textOf).join('').trim()
  return /^(?:(?:instagram|אינסטגרם|מקור|source|קישור(?:\s+למתכון)?)\s*)?[:：.\s]*$/iu.test(rest)
}

function imageParagraph(node) {
  return node.type === 'paragraph' && node.children.some(child => child.type === 'image') &&
    node.children.every(child => child.type === 'image' || (child.type === 'text' && !child.value.trim()))
}

function instagramCode(url) {
  if (!isInstagramUrl(url)) return null
  return new URL(url).pathname.match(/^\/(?:p|reel|tv)\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1] ?? null
}

function imageCode(url) {
  try {
    const path = new URL(url, 'https://recipe.local').pathname
    return path.match(/^\/recipe-images\/([A-Za-z0-9_-]+)\.(?:jpe?g|png|webp|avif)$/i)?.[1] ?? null
  } catch {
    return null
  }
}

function sourceCodes(nodes) {
  const codes = new Set()
  function visit(node) {
    if (node.type === 'link') {
      const code = instagramCode(node.url)
      if (code) codes.add(code)
    }
    node.children?.forEach(visit)
  }
  nodes.forEach(visit)
  return codes
}

function withoutMovedImages(node, movedImages) {
  if (!imageParagraph(node)) return node
  const children = node.children.filter(child => !movedImages.has(child))
  return children.some(child => child.type === 'image') ? { ...node, children } : null
}

function element(tag, className, children) {
  return { type: 'blockquote', data: { hName: tag, hProperties: { className } }, children }
}

function styleSectionHeading(node) {
  return { ...node, data: { ...node.data, hName: 'h4', hProperties: { className: 'recipe-section-heading' } } }
}

function makeCard(recipe, nodes, photos, movedImages) {
  const originalTitle = nodes[recipe.start]
  const title = {
    ...originalTitle,
    data: { ...originalTitle.data, hName: 'h3', hProperties: { className: 'recipe-card-title' } },
  }
  const groups = []
  let currentGroup = null
  for (const originalNode of nodes.slice(recipe.start + 1, recipe.end)) {
    const node = withoutMovedImages(originalNode, movedImages)
    if (!node) continue
    if (node.type === 'thematicBreak') {
      // Preserve internal separators. The card border replaces the trailing one.
      if (node !== nodes[recipe.end - 1]) (currentGroup?.children ?? groups).push(node)
      continue
    }
    const kind = sectionKind(node)
    if (kind) {
      currentGroup = element('section', `recipe-${kind}`, [styleSectionHeading(node)])
      groups.push(currentGroup)
    } else if (isSourceParagraph(node)) {
      currentGroup = null
      groups.push(element('footer', 'recipe-card-source', [node]))
    } else {
      (currentGroup?.children ?? groups).push(node)
    }
  }
  const children = [element('header', 'recipe-card-heading', [title])]
  if (photos.length) children.push(element('div', 'recipe-card-photos', photos))
  children.push(element('div', 'recipe-card-body', groups))
  return element('article', 'recipe-card', children)
}

export default function recipeMarkdown() {
  return tree => {
    const label = /[\u0590-\u05ff]/u.test(textOf(tree)) ? 'למתכון באינסטגרם' : 'View on Instagram'
    formatInstagramLinks(tree, label)
    const nodes = tree.children
    const candidates = nodes.flatMap((node, index) => {
      const title = recipeTitle(node)
      return title ? [{ start: index, title }] : []
    })
    const recipes = []
    candidates.forEach((candidate, index) => {
      const limit = candidates[index + 1]?.start ?? nodes.length
      let end = limit
      const seenSections = new Set()
      for (let cursor = candidate.start + 1; cursor < limit; cursor += 1) {
        seenSections.add(sectionKind(nodes[cursor]))
        if (isSourceParagraph(nodes[cursor]) && seenSections.has('ingredients') && seenSections.has('method')) {
          end = cursor + 1
          break
        }
      }
      // Tail galleries belong to the answer until a photo can be associated.
      // Images earlier in a recipe remain valid body content.
      while (end > candidate.start + 1 && (imageParagraph(nodes[end - 1]) || nodes[end - 1].type === 'thematicBreak')) end -= 1
      const sections = nodes.slice(candidate.start + 1, end).map(sectionKind)
      if (sections.includes('ingredients') && sections.includes('method')) {
        recipes.push({
          ...candidate, end,
          normalizedTitle: normalizeTitle(candidate.title),
          sourceCodes: sourceCodes(nodes.slice(candidate.start + 1, end)),
          photos: [],
        })
      }
    })
    if (!recipes.length) return

    // API images are appended after the answer. A saved Instagram post ID is
    // stronger evidence than wording, which the assistant may slightly change.
    // Conflicting or ambiguous evidence never causes a photo to move.
    const movedImages = new Set()
    for (const node of nodes) {
      if (!imageParagraph(node)) continue
      for (const image of node.children.filter(child => child.type === 'image')) {
        const name = normalizeTitle(image.alt || image.title || '')
        const code = imageCode(image.url)
        const nameMatches = name ? recipes.filter(recipe => recipe.normalizedTitle === name) : []
        const codeMatches = code ? recipes.filter(recipe => recipe.sourceCodes.has(code)) : []
        let match = null
        if (codeMatches.length === 1 && (nameMatches.length !== 1 || nameMatches[0] === codeMatches[0])) match = codeMatches[0]
        else if (!codeMatches.length && nameMatches.length === 1) match = nameMatches[0]
        if (match) {
          match.photos.push({ type: 'paragraph', children: [image] })
          movedImages.add(image)
        }
      }
    }

    const output = []
    for (let cursor = 0; cursor < nodes.length;) {
      const recipe = recipes.find(item => item.start === cursor)
      if (recipe) {
        output.push(makeCard(recipe, nodes, recipe.photos, movedImages))
        cursor = recipe.end
        // A divider immediately after a card duplicates its border.
        if (nodes[cursor]?.type === 'thematicBreak') cursor += 1
      } else {
        const node = withoutMovedImages(nodes[cursor], movedImages)
        if (node) output.push(node)
        cursor += 1
      }
    }
    tree.children = output
  }
}
