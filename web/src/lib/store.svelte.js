// App state + domain helpers. `S` mirrors GET /api/state; `UI` is client-only.
// Every mutation goes through mutate(), which refetches the whole state
// afterwards (simple, always consistent; contact ids are list indices).

import { api, post } from './api.js'
import { toast } from './toast.svelte.js'
import { addMonths } from './cal.js'

// Index + 1 is the keyboard shortcut (1–6). Calendar and Timeline sit between
// Due and Import in the sidebar but take shortcuts 5 and 6 so Import stays on 4.
export const VIEWS = ['board', 'contacts', 'due', 'import', 'calendar', 'timeline']
export const CLOSED = new Set(['won', 'lost', 'dormant'])
const HUES = [222, 262, 300, 190, 28, 168, 340, 48]
/** Days in the current stage after which an open contact counts as "stuck". */
export const STUCK_DAYS = 30

export const S = $state({
  contacts: [],
  stages: [],
  sources: [],
  today: '',
  meetingsFetchedAt: '', // when the meetings cache was last refreshed from mail
  loaded: false,
  error: null,
})

function initialView() {
  try {
    const v = localStorage.getItem('crm.view')
    if (VIEWS.includes(v)) return v
  } catch (e) {
    /* private mode etc. */
  }
  return 'board'
}

export const UI = $state({
  view: initialView(),
  qInput: '', // what's in the search box
  q: '', // debounced copy used for filtering
  filters: { stages: [], source: '', overdue: false, noNext: false },
  sort: { key: 'name', dir: 1 },
  drawer: { id: null, tab: 'details', editingNext: false },
  // Calendar view: shown month ("YYYY-MM") and selected day for the agenda;
  // empty = today. Not persisted.
  cal: { month: '', selected: '' },
  // Timeline view: axis range ('1M' | '3M' | '6M' | '12M' | 'All'), row sort
  // ('longest' | 'recent' | 'name' | 'stage'), hide won/lost/dormant. Not persisted.
  tl: { range: '3M', sort: 'longest', hideClosed: true },
})

/** email(lowercase) -> {loading:true} | {messages:[...]} | {error:string} */
export const threadCache = $state({})

// ---------- domain helpers ----------
export const byId = (id) => S.contacts.find((c) => c.id === id)

export function daysUntil(iso) {
  const a = Date.parse(S.today + 'T00:00:00')
  const b = Date.parse(iso + 'T00:00:00')
  return isNaN(a) || isNaN(b) ? NaN : Math.round((b - a) / 86400000)
}

/** "" | "overdue" | "soon" | "normal" */
export function dueStatus(c) {
  if (!c.next_date) return ''
  if (c.next_date < S.today) return 'overdue'
  const d = daysUntil(c.next_date)
  return !isNaN(d) && d <= 7 ? 'soon' : 'normal'
}

export function relDue(iso) {
  const d = daysUntil(iso)
  if (isNaN(d)) return ''
  if (d === 0) return 'today'
  if (d === 1) return 'tomorrow'
  if (d === -1) return 'yesterday'
  if (d < 0) return -d + 'd overdue'
  if (d <= 14) return 'in ' + d + 'd'
  return ''
}

export function dateWithRel(iso) {
  const r = relDue(iso)
  return r ? iso + ' · ' + r : iso
}

export const hasNext = (c) => !!(c.next_action || c.next_date)

/**
 * Format a cached meeting (`c.next_meeting`) for display:
 * { day: "Fri 12 Sep", time: "09:00" | "", rel: "today"|"tomorrow"|"in 3d"|"",
 *   soon: boolean }. `start` is a local "YYYY-MM-DD HH:MM" (or date-only when all-day).
 */
export function meetingWhen(m) {
  if (!m || !m.start) return null
  const [d, t = ''] = m.start.split(' ')
  const dt = new Date(d + 'T00:00:00')
  const day = isNaN(dt) ? d : dt.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  const n = daysUntil(d)
  return { day, time: m.all_day ? '' : t, rel: relDue(d), soon: !isNaN(n) && n >= 0 && n <= 7 }
}

/** Hue/saturation for a stage: by position in the configured list, with won/lost/dormant fixed. */
export function stageStyle(stage) {
  const i = S.stages.indexOf(stage)
  const s = (stage || '').toLowerCase()
  let h = HUES[(i < 0 ? 0 : i) % HUES.length]
  let sat = 70
  if (s === 'won') h = 145
  else if (s === 'lost') h = 4
  else if (s === 'dormant') sat = 0
  return { h, sat }
}

export const isClosed = (c) => CLOSED.has((c.stage || '').toLowerCase())

// ---------- stage history ----------
/** Day number (UTC days since epoch) of "YYYY-MM-DD"; NaN if unparsable. */
export const dayNum = (iso) => Math.floor(Date.parse(String(iso || '').slice(0, 10) + 'T00:00:00Z') / 86400000)

/**
 * A contact's stage segments, chronological, last = current stage (end null =
 * ongoing through today). The server sends `segments`; if it's missing or empty
 * (older server) fall back to one ongoing segment from the earliest note date,
 * or today. Starts after today are clamped to today; malformed entries dropped.
 */
export function segmentsOf(c) {
  const today = S.today
  let segs = Array.isArray(c.segments) ? c.segments.filter((s) => s && !isNaN(dayNum(s.start))) : []
  if (!segs.length) {
    const notes = Array.isArray(c.notes) ? c.notes : []
    let start = ''
    for (const n of notes) {
      const d = String((n && n.date) || '').slice(0, 10)
      if (!isNaN(dayNum(d)) && (!start || d < start)) start = d
    }
    segs = [{ stage: c.stage, start: start || today, end: null }]
  }
  return segs.map((s, i) => {
    let start = String(s.start).slice(0, 10)
    if (today && start > today) start = today
    let end = i === segs.length - 1 ? null : String(s.end || segs[i + 1].start).slice(0, 10)
    if (end !== null && end < start) end = start
    return { stage: s.stage || c.stage, start, end }
  })
}

/** Days spent in the current stage so far (0 if it started today). */
export function daysInStage(c) {
  const segs = segmentsOf(c)
  const d = dayNum(S.today) - dayNum(segs[segs.length - 1].start)
  return isNaN(d) ? 0 : Math.max(0, d)
}

/** Open (not won/lost/dormant) and in the current stage for ≥ STUCK_DAYS. */
export const isStuck = (c) => !isClosed(c) && daysInStage(c) >= STUCK_DAYS

export const subline = (c) => [c.company, c.role].filter(Boolean).join(' · ')
export const cmpStr = (a, b) => (a || '').localeCompare(b || '', undefined, { sensitivity: 'base' })

export function matchesSearch(c) {
  const q = UI.q.trim().toLowerCase()
  if (!q) return true
  return [c.name, c.company, c.email, c.role, c.stage, c.source].some((v) =>
    (v || '').toLowerCase().includes(q),
  )
}

export function matchesFilters(c, { ignoreNoNext = false } = {}) {
  const f = UI.filters
  if (f.stages.length && !f.stages.includes(c.stage)) return false
  if (f.source && c.source !== f.source) return false
  if (f.overdue && dueStatus(c) !== 'overdue') return false
  if (!ignoreNoNext && f.noNext && hasNext(c)) return false
  return true
}

export const visibleContacts = () => S.contacts.filter((c) => matchesSearch(c) && matchesFilters(c))
export const filtersActive = () =>
  !!(UI.filters.stages.length || UI.filters.source || UI.filters.overdue || UI.filters.noNext)

// ---------- UI actions ----------
export function setView(v) {
  if (!VIEWS.includes(v)) return
  UI.view = v
  try {
    localStorage.setItem('crm.view', v)
  } catch (e) {
    /* ignore */
  }
  window.scrollTo({ top: 0 })
}

export function clearSearch() {
  UI.qInput = ''
  UI.q = ''
}

export function resetFilters() {
  UI.filters = { stages: [], source: '', overdue: false, noNext: false }
  clearSearch()
}

export function toggleStageFilter(st) {
  const i = UI.filters.stages.indexOf(st)
  if (i >= 0) UI.filters.stages.splice(i, 1)
  else UI.filters.stages.push(st)
}

export function toggleSort(key) {
  if (UI.sort.key === key) UI.sort.dir = -UI.sort.dir
  else UI.sort = { key, dir: 1 }
}

export function openDrawer(id, tab) {
  if (UI.drawer.id !== id) UI.drawer = { id, tab: tab || 'details', editingNext: false }
  else if (tab) UI.drawer.tab = tab
}

export function closeDrawer() {
  UI.drawer = { id: null, tab: 'details', editingNext: false }
}

// ---------- calendar ----------
export const calMonth = () => UI.cal.month || (S.today || '').slice(0, 7)
export const calSelected = () => UI.cal.selected || S.today

export function calShift(n) {
  if (!S.today) return
  UI.cal.month = addMonths(calMonth(), n)
}

export function calToday() {
  UI.cal.month = (S.today || '').slice(0, 7)
  UI.cal.selected = S.today
}

/** Select a day for the agenda (and show its month). */
export function calSelect(iso) {
  UI.cal.selected = iso
  UI.cal.month = iso.slice(0, 7)
}

// ---------- server I/O ----------
export async function loadState() {
  try {
    const j = await api('/api/state')
    S.contacts = j.contacts || []
    S.stages = j.stages || []
    S.sources = j.sources || []
    S.today = j.today || ''
    S.meetingsFetchedAt = j.meetings_fetched_at || ''
    S.loaded = true
    S.error = null
  } catch (e) {
    S.error = e.message
  }
}

/**
 * POST a mutation, toast, then reload the whole state.
 * Returns the response, or null on failure (already toasted).
 * 409 means the data changed under us: reload and ask the user to retry.
 */
export async function mutate(path, body, okMsg) {
  try {
    const r = await post(path, body)
    if (okMsg) toast(okMsg, 'ok')
    await loadState()
    return r
  } catch (e) {
    if (e.status === 409) {
      toast('Changed elsewhere — reloaded. Try again.', 'warn')
      await loadState()
    } else toast(e.message, 'error')
    return null
  }
}

/**
 * Rescan the mailbox for upcoming meetings and rebuild the (synced) cache.
 * Reloads state on success so cards pick up the new `next_meeting`.
 */
export async function refreshMeetings() {
  const r = await mutate('/api/meetings/refresh', {}, null)
  if (r) toast(`Synced ${r.count} upcoming meeting${r.count === 1 ? '' : 's'}`, 'ok')
  return r
}

export async function loadThread(email) {
  const key = email.toLowerCase()
  threadCache[key] = { loading: true }
  try {
    const r = await api('/api/thread?email=' + encodeURIComponent(email))
    threadCache[key] = { messages: r.messages || [] }
  } catch (e) {
    threadCache[key] = { error: e.message }
  }
}
