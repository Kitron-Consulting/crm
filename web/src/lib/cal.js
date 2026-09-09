// Pure date helpers for the Calendar view. Days are "YYYY-MM-DD" strings and
// months "YYYY-MM"; everything is computed in UTC so the local zone/DST can
// never shift a day. Weeks are Monday-first (Finnish convention).

const DAY = 86400000
const utc = (iso) => {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, (m || 1) - 1, d || 1))
}
const fmt = (dt) => dt.toISOString().slice(0, 10)

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export const addDays = (iso, n) => fmt(new Date(utc(iso).getTime() + n * DAY))

export function addMonths(ym, n) {
  const [y, m] = ym.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 7)
}

/** "September 2026" */
export const monthTitle = (ym) =>
  utc(ym + '-01').toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** ISO-8601 week number (weeks start Monday; week 1 contains the first Thursday). */
export function isoWeek(iso) {
  const d = utc(iso)
  const dayNum = d.getUTCDay() || 7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum)
  const yearStart = Date.UTC(d.getUTCFullYear(), 0, 1)
  return Math.ceil(((d - yearStart) / DAY + 1) / 7)
}

/**
 * Always 6 Monday-first weeks covering `ym` (so the grid never changes height):
 * [{ week, days: [{ iso, day, inMonth, weekend }] }]
 */
export function monthGrid(ym) {
  const first = utc(ym + '-01')
  const lead = (first.getUTCDay() + 6) % 7 // days before the 1st in its week (Mon=0)
  let cur = new Date(first.getTime() - lead * DAY)
  const weeks = []
  for (let w = 0; w < 6; w++) {
    const days = []
    for (let i = 0; i < 7; i++) {
      const iso = fmt(cur)
      days.push({ iso, day: cur.getUTCDate(), inMonth: iso.slice(0, 7) === ym, weekend: i >= 5 })
      cur = new Date(cur.getTime() + DAY)
    }
    weeks.push({ week: isoWeek(days[0].iso), days })
  }
  return weeks
}
