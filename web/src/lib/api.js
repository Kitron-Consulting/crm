// Thin fetch wrapper around the JSON API in crm/web.py.
// The token is read ONCE from the URL `crm serve` printed and sent on every request.

export const TOKEN = new URLSearchParams(location.search).get('t') || ''

export async function api(path, opts = {}) {
  opts.headers = Object.assign(
    { 'X-CRM-Token': TOKEN, 'Content-Type': 'application/json' },
    opts.headers || {},
  )
  const r = await fetch(path, opts)
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const e = new Error(j.error || 'HTTP ' + r.status)
    e.status = r.status
    throw e
  }
  return j
}

export const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) })

// Href for an IMAP attachment. Plain <a href> can't send X-CRM-Token, so the
// token travels in the `t` query param (the server accepts either). The server
// serves previewable types (pdf, common images, text/plain) inline; `download`
// appends `&download=1` to force Content-Disposition: attachment for any type.
export const attachmentUrl = (m, a, { download = false } = {}) =>
  '/api/attachment?folder=' + encodeURIComponent(m.folder || 'INBOX') +
  '&uid=' + encodeURIComponent(m.uid || '') +
  '&part=' + encodeURIComponent(a.part || '') +
  '&t=' + encodeURIComponent(TOKEN) +
  (download ? '&download=1' : '')
