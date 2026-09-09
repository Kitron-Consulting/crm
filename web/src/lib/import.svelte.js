// Import view state lives here (not in the component) so switching views
// doesn't throw away a slow IMAP scan or the user's inline edits.

import { api, post } from './api.js'
import { S, loadState } from './store.svelte.js'
import { toast } from './toast.svelte.js'
import { confirmDialog } from './modal.svelte.js'

export const IMP = $state({
  days: '30',
  status: 'idle', // idle | loading | error | done
  error: null,
  rows: [],
  filter: '',
  exhausted: false, // every candidate of the last scan was added/ignored
})

export function visibleRows() {
  const q = IMP.filter.trim().toLowerCase()
  if (!q) return IMP.rows
  return IMP.rows.filter((r) => [r.email, r.name, r.company].some((v) => (v || '').toLowerCase().includes(q)))
}

export async function scan() {
  IMP.status = 'loading'
  IMP.error = null
  IMP.rows = []
  IMP.exhausted = false
  // bind:value on <input type="number"> yields a number (or null when cleared),
  // not a string — never call string methods on it. Parse, then stringify.
  const n = Number.parseInt(String(IMP.days ?? '').trim(), 10)
  const days = Number.isFinite(n) && n > 0 ? String(n) : ''
  try {
    const res = await api('/api/import/scan' + (days ? '?days=' + encodeURIComponent(days) : ''))
    const stages = S.stages.length ? S.stages : ['contacted']
    const sources = S.sources.length ? S.sources : ['cold']
    IMP.rows = (res.candidates || []).map((c) => ({
      email: c.email || '',
      add: true,
      name: c.name || (c.email || '').split('@')[0],
      company: c.company || '',
      role: '',
      stage: stages.includes('contacted') ? 'contacted' : stages[0],
      source: sources.includes('cold') ? 'cold' : sources[0],
    }))
    IMP.status = 'done'
  } catch (e) {
    IMP.error = e.message
    IMP.status = 'error'
  }
}

export function setAll(on) {
  for (const r of visibleRows()) r.add = on
}

/** Add (or ignore) the checked, visible rows. */
export async function commit(asIgnore) {
  const picked = visibleRows().filter((r) => r.add)
  if (!picked.length) {
    toast('Nothing checked.', 'info')
    return
  }
  if (asIgnore) {
    const ok = await confirmDialog({
      title: 'Ignore ' + picked.length + ' address' + (picked.length === 1 ? '' : 'es') + '?',
      body: "They won't be suggested again in future scans. You can still add them manually later.",
      ok: 'Ignore',
      danger: true,
    })
    if (!ok) return
  }
  const body = asIgnore
    ? { ignore: picked.map((r) => r.email) }
    : {
        add: picked.map((r) => ({
          email: r.email,
          name: r.name.trim(),
          company: r.company.trim(),
          role: r.role.trim(),
          stage: r.stage,
          source: r.source,
        })),
      }
  let res
  try {
    res = await post('/api/import/commit', body)
  } catch (e) {
    toast(e.message, 'error')
    return
  }
  IMP.rows = IMP.rows.filter((r) => !picked.includes(r))
  toast(
    asIgnore
      ? 'Ignored ' + res.ignored + '.'
      : 'Added ' + res.added + ' contact' + (res.added === 1 ? '' : 's') + '.',
    'ok',
  )
  if (!IMP.rows.length) IMP.exhausted = true
  if (!asIgnore) await loadState()
}
