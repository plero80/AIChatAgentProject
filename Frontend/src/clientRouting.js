// A page belongs to one client. No profile or graph settings come from the browser.
export function clientSlug(pathname = globalThis.location?.pathname || '/') {
  if (pathname === '/') return null
  const match = pathname.match(/^\/c\/([a-z][a-z0-9-]{0,63})\/?$/)
  if (!match) throw new Error('Unknown client URL')
  return match[1]
}

export function clientApiPath(resource, slug = clientSlug()) {
  return slug ? `/api/clients/${encodeURIComponent(slug)}/${resource}` : `/${resource}`
}
