<script>
  // Stage-history timeline: one row per contact, bars = time spent in each
  // stage (server `segments`, or a notes-derived fallback). Reveals stuck deals
  // and cycle times — the past dimension the board (now) and calendar (next)
  // can't show. Read-only; click a row to open the drawer.
  //
  // First cut (built during a handoff): functional bars + today line + range +
  // sort + month ticks + native-title tooltips. Polish left for later — a rich
  // hover card, week gridlines at short ranges, and a duration summary per stage.
  import ViewHead from '../components/ViewHead.svelte'
  import FiltersBar from '../components/FiltersBar.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import SkeletonRows from '../components/SkeletonRows.svelte'
  import StageChip from '../components/StageChip.svelte'
  import {
    S, UI, matchesSearch, matchesFilters, isClosed, isStuck, daysInStage,
    segmentsOf, dayNum, stageStyle, subline, cmpStr, openDrawer,
  } from '../lib/store.svelte.js'
  import { addMonths } from '../lib/cal.js'

  const RANGES = ['1M', '3M', '6M', '12M', 'All']
  const MONTHS = { '1M': 1, '3M': 3, '6M': 6, '12M': 12 }

  // Search + stage/source/overdue filters; "Hide closed" (won/lost/dormant).
  const rows = $derived.by(() => {
    let cs = S.contacts.filter((c) => matchesSearch(c) && matchesFilters(c, { ignoreNoNext: true }))
    if (UI.tl.hideClosed) cs = cs.filter((c) => !isClosed(c))
    return cs.map((c) => ({ c, segs: segmentsOf(c), stuck: isStuck(c), days: daysInStage(c) }))
  })

  const sorted = $derived.by(() => {
    const r = [...rows]
    const s = UI.tl.sort
    if (s === 'name') r.sort((a, b) => cmpStr(a.c.name, b.c.name))
    else if (s === 'stage') r.sort((a, b) => S.stages.indexOf(a.c.stage) - S.stages.indexOf(b.c.stage) || cmpStr(a.c.name, b.c.name))
    else if (s === 'recent') r.sort((a, b) => dayNum(b.segs.at(-1).start) - dayNum(a.segs.at(-1).start))
    else r.sort((a, b) => b.days - a.days) // longest in stage
    return r
  })

  // Axis: ends today, starts N months back — or the earliest segment for "All".
  const axis = $derived.by(() => {
    const end = dayNum(S.today)
    let start
    if (UI.tl.range === 'All') {
      const mins = rows.map((r) => dayNum(r.segs[0].start)).filter((n) => !isNaN(n))
      start = mins.length ? Math.min(...mins) : end - 90
    } else {
      start = dayNum(addMonths((S.today || '').slice(0, 7), -MONTHS[UI.tl.range]) + '-01')
    }
    if (!(end > start)) start = end - 30
    return { start, end, span: end - start }
  })

  const pct = (iso) => Math.max(0, Math.min(100, ((dayNum(iso) - axis.start) / axis.span) * 100))
  const todayX = $derived(pct(S.today))

  // Month tick labels across the axis.
  const ticks = $derived.by(() => {
    const out = []
    let ym = (S.today || '2026-01').slice(0, 7)
    // walk back to before the axis start
    while (dayNum(ym + '-01') > axis.start) ym = addMonths(ym, -1)
    for (let i = 0; i < 60; i++) {
      const d = ym + '-01'
      if (dayNum(d) > axis.end) break
      if (dayNum(d) >= axis.start) out.push({ x: pct(d), label: new Date(d + 'T00:00:00Z').toLocaleDateString(undefined, { month: 'short', year: '2-digit' }) })
      ym = addMonths(ym, 1)
    }
    return out
  })

  const summary = $derived.by(() => {
    const n = rows.length
    const stuck = rows.filter((r) => r.stuck).length
    const ds = rows.map((r) => r.days).sort((a, b) => a - b)
    const median = ds.length ? ds[Math.floor(ds.length / 2)] : 0
    return { n, stuck, median }
  })

  const barTitle = (c, s, i, segs) => {
    const end = s.end || S.today
    const days = Math.max(0, dayNum(end) - dayNum(s.start))
    const ongoing = i === segs.length - 1
    return `${s.stage} · ${s.start}${ongoing ? ` – ongoing (${days}d)` : ` – ${s.end} (${days}d)`}`
  }
</script>

<ViewHead title="Timeline" sub={`Stage history · today is ${S.today}`}>
  <div class="seg-btns">
    {#each RANGES as r}
      <button type="button" class="btn sm" class:on={UI.tl.range === r} onclick={() => (UI.tl.range = r)}>{r}</button>
    {/each}
  </div>
  <select class="ctl" bind:value={UI.tl.sort} aria-label="Sort">
    <option value="longest">Longest in stage</option>
    <option value="recent">Recently moved</option>
    <option value="name">Name</option>
    <option value="stage">Stage order</option>
  </select>
</ViewHead>

<FiltersBar shown={rows.length} noNext={false} label={`${summary.n} contacts · ${summary.stuck} stuck ≥${30}d · median ${summary.median}d in stage`}>
  <button type="button" class="btn sm" class:on={UI.tl.hideClosed} onclick={() => (UI.tl.hideClosed = !UI.tl.hideClosed)}>Hide closed</button>
</FiltersBar>

{#if !S.loaded}
  <SkeletonRows rows={8} />
{:else if !sorted.length}
  <EmptyState title="No contacts" hint="Nothing matches the current filters." />
{:else}
  <div class="tl">
    <div class="tl-axis">
      <div class="tl-label"></div>
      <div class="tl-track tl-ticks">
        {#each ticks as t}<span class="tl-tick" style:left="{t.x}%">{t.label}</span>{/each}
        <span class="tl-today" style:left="{todayX}%" title="today"></span>
      </div>
    </div>
    <div class="tl-rows">
      {#each sorted as r (r.c.id)}
        <div class="tl-row" role="button" tabindex="0"
             onclick={() => openDrawer(r.c.id)}
             onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), openDrawer(r.c.id))}>
          <div class="tl-label">
            <span class="tl-name" title={r.c.name}>{r.c.name || '(no name)'}</span>
            <span class="tl-sub" title={subline(r.c)}>{subline(r.c)}</span>
          </div>
          <div class="tl-track">
            {#each r.segs as s, i (i)}
              {@const t = stageStyle(s.stage)}
              {@const left = pct(s.start)}
              {@const right = pct(s.end || S.today)}
              <div class="tl-bar tint" class:ongoing={i === r.segs.length - 1}
                   style:--h={t.h} style:--s={t.sat}
                   style:left="{left}%" style:width="{Math.max(0.4, right - left)}%"
                   title={barTitle(r.c, s, i, r.segs)}>
                <span class="tl-bar-txt">{s.stage}</span>
              </div>
            {/each}
            {#if r.stuck}<span class="tl-stuck" title="{r.days} days in {r.c.stage}">{r.days}d</span>{/if}
            <span class="tl-today" style:left="{todayX}%"></span>
          </div>
        </div>
      {/each}
    </div>
  </div>
{/if}
