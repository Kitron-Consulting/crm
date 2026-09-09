<script>
  // Stage-history timeline: one row per contact, bars = time spent in each
  // stage (server `segments`, or a notes-derived fallback). Reveals stuck deals
  // and cycle times — the past dimension the board (now) and calendar (next)
  // can't show. Read-only; click a row to open the drawer.
  //
  // Polish over the first cut: a rich pointer-following hover card (was native
  // `title`), week gridlines at short ranges, and a per-stage duration summary.
  import ViewHead from '../components/ViewHead.svelte'
  import FiltersBar from '../components/FiltersBar.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import SkeletonRows from '../components/SkeletonRows.svelte'
  import {
    S, UI, STUCK_DAYS, matchesSearch, matchesFilters, isClosed, isStuck, daysInStage,
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
  const pctDay = (d) => ((d - axis.start) / axis.span) * 100
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

  // Monday gridlines, but only at short ranges where a whole month of them stays
  // legible. Epoch day 0 (1970-01-01) is a Thursday, so Monday is weekday 4.
  const weekLines = $derived.by(() => {
    if (UI.tl.range !== '1M' && UI.tl.range !== '3M') return []
    const out = []
    let d = axis.start
    while ((((d % 7) + 7) % 7) !== 4) d++ // advance to the first Monday in range
    for (; d <= axis.end; d += 7) out.push(pctDay(d))
    return out
  })

  const median = (ds) => (ds.length ? [...ds].sort((a, b) => a - b)[Math.floor(ds.length / 2)] : 0)

  const summary = $derived.by(() => {
    const n = rows.length
    const stuck = rows.filter((r) => r.stuck).length
    return { n, stuck, median: median(rows.map((r) => r.days)) }
  })

  // Per-stage occupancy: how many contacts sit in each stage now, and the median
  // days they've been there — in configured stage order. Reveals where deals pile up.
  const stageSummary = $derived.by(() => {
    const m = new Map()
    for (const r of rows) {
      if (!m.has(r.c.stage)) m.set(r.c.stage, [])
      m.get(r.c.stage).push(r.days)
    }
    const known = S.stages.filter((st) => m.has(st))
    const extra = [...m.keys()].filter((st) => !S.stages.includes(st)).sort(cmpStr)
    return [...known, ...extra].map((st) => ({ stage: st, n: m.get(st).length, median: median(m.get(st)) }))
  })

  // ---------- hover card ----------
  const CARD_W = 240
  let hover = $state(null)
  function showHover(e, c, s, i, segs) {
    const ongoing = i === segs.length - 1
    const end = s.end || S.today
    const days = Math.max(0, dayNum(end) - dayNum(s.start))
    const t = stageStyle(s.stage)
    hover = {
      name: c.name || '(no name)', sub: subline(c), stage: s.stage,
      start: s.start, end, ongoing, days,
      stuck: ongoing && !isClosed(c) && days >= STUCK_DAYS,
      h: t.h, s: t.sat, x: 0, y: 0,
    }
    moveHover(e)
  }
  function moveHover(e) {
    if (!hover) return
    hover.x = Math.min(e.clientX + 14, window.innerWidth - CARD_W - 8)
    hover.y = e.clientY
  }
  const hideHover = () => (hover = null)
  const weeks = (d) => (d >= 14 ? ` · ${Math.round(d / 7)}w` : '')
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

<FiltersBar shown={rows.length} noNext={false} label={`${summary.n} contacts · ${summary.stuck} stuck ≥${STUCK_DAYS}d · median ${summary.median}d in stage`}>
  <button type="button" class="btn sm" class:on={UI.tl.hideClosed} onclick={() => (UI.tl.hideClosed = !UI.tl.hideClosed)}>Hide closed</button>
</FiltersBar>

{#if !S.loaded}
  <SkeletonRows rows={8} />
{:else if !sorted.length}
  <EmptyState title="No contacts" hint="Nothing matches the current filters." />
{:else}
  {#if stageSummary.length}
    <div class="tl-summary" aria-label="Contacts per stage now">
      {#each stageSummary as g (g.stage)}
        {@const t = stageStyle(g.stage)}
        <div class="tl-sumchip tint" style:--h={t.h} style:--s={t.sat} title="{g.n} in {g.stage} · median {g.median}d">
          <span class="tl-sumdot"></span>
          <span class="tl-sumname">{g.stage}</span>
          <span class="tl-sumn">{g.n}</span>
          <span class="tl-summed">~{g.median}d</span>
        </div>
      {/each}
    </div>
  {/if}
  <div class="tl">
    <div class="tl-axis">
      <div class="tl-label"></div>
      <div class="tl-track tl-ticks">
        {#each weekLines as x}<span class="tl-week" style:left="{x}%"></span>{/each}
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
            {#each weekLines as x}<span class="tl-week" style:left="{x}%"></span>{/each}
            {#each r.segs as s, i (i)}
              {@const t = stageStyle(s.stage)}
              {@const left = pct(s.start)}
              {@const right = pct(s.end || S.today)}
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <div class="tl-bar tint" class:ongoing={i === r.segs.length - 1}
                   style:--h={t.h} style:--s={t.sat}
                   style:left="{left}%" style:width="{Math.max(0.4, right - left)}%"
                   onpointerenter={(e) => showHover(e, r.c, s, i, r.segs)}
                   onpointermove={moveHover}
                   onpointerleave={hideHover}>
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

  {#if hover}
    <div class="tl-card tint" style:--h={hover.h} style:--s={hover.s} style:left="{hover.x}px" style:top="{hover.y}px">
      <div class="tl-card-head">
        <span class="tl-sumdot"></span>
        <span class="tl-card-stage">{hover.stage}</span>
        <span class="tl-card-tag">{hover.ongoing ? 'current' : 'past'}</span>
      </div>
      {#if hover.sub}<div class="tl-card-sub">{hover.sub}</div>{/if}
      <div class="tl-card-range">{hover.start} → {hover.ongoing ? 'today' : hover.end}</div>
      <div class="tl-card-dur">{hover.days}d{weeks(hover.days)} in stage</div>
      {#if hover.stuck}<div class="tl-card-stuck">Stuck ≥ {STUCK_DAYS}d</div>{/if}
    </div>
  {/if}
{/if}
