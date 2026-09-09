<script>
  // Month grid of next actions. Drag a chip onto a day to reschedule (POST
  // /api/contacts/next with the same action), click an empty day to schedule
  // someone, drag an Unscheduled contact onto a day to schedule them.
  import ViewHead from '../components/ViewHead.svelte'
  import FiltersBar from '../components/FiltersBar.svelte'
  import ErrorBox from '../components/ErrorBox.svelte'
  import SkeletonRows from '../components/SkeletonRows.svelte'
  import StageChip from '../components/StageChip.svelte'
  import Btn from '../components/Btn.svelte'
  import Icon from '../components/Icon.svelte'
  import {
    S, UI, CLOSED, byId, matchesSearch, matchesFilters, dueStatus, stageStyle, subline, cmpStr, dateWithRel,
    openDrawer, mutate, loadState, calMonth, calSelected, calShift, calToday, calSelect,
  } from '../lib/store.svelte.js'
  import { setNextDialog } from '../lib/modal.svelte.js'
  import { monthGrid, monthTitle, WEEKDAYS } from '../lib/cal.js'

  const MAX_CHIPS = 3
  const ym = $derived(calMonth())
  const weeks = $derived(monthGrid(ym))
  const selected = $derived(calSelected())

  // Search + stage/source/overdue filters apply to chips; "No next action" is
  // meaningless here (see the Unscheduled panel) and ignored.
  const pass = (c) => matchesSearch(c) && matchesFilters(c, { ignoreNoNext: true })
  const scheduled = $derived(S.contacts.filter((c) => c.next_date && pass(c)))
  const byDay = $derived.by(() => {
    const m = new Map()
    for (const c of scheduled) {
      if (!m.has(c.next_date)) m.set(c.next_date, [])
      m.get(c.next_date).push(c)
    }
    for (const v of m.values()) v.sort((a, b) => cmpStr(a.name, b.name))
    return m
  })
  const inMonth = $derived(scheduled.filter((c) => c.next_date.slice(0, 7) === ym).length)
  const agenda = $derived(byDay.get(selected) || [])
  const unscheduled = $derived(
    S.contacts
      .filter((c) => !c.next_date && !CLOSED.has((c.stage || '').toLowerCase()) && pass(c))
      .sort((a, b) => cmpStr(a.name, b.name)),
  )
  /** "overdue" | "today" | "" — colour of a chip by its due status. */
  const chipStatus = (c) => (c.next_date < S.today ? 'overdue' : c.next_date === S.today ? 'today' : '')
  const chipTitle = (c) =>
    (c.name || '') + (c.company ? ' · ' + c.company : '') + (c.next_action ? ' — ' + c.next_action : '') +
    (c.next_date ? ' (' + dateWithRel(c.next_date) + ')' : '')

  // ---------- drag & drop ----------
  let drag = $state(null) // { id, kind: 'chip' | 'unsched' }
  let dropDay = $state(null)
  $effect(() => {
    document.body.classList.toggle('dragging-any', drag !== null)
    return () => document.body.classList.remove('dragging-any')
  })
  function dragstart(e, c, kind) {
    drag = { id: c.id, kind }
    e.dataTransfer.setData('text/plain', String(c.id))
    e.dataTransfer.effectAllowed = 'move'
  }
  function dragend() {
    drag = null
    dropDay = null
  }
  function dragover(e, iso) {
    if (!drag) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    dropDay = iso
  }
  function dragleave(e, iso) {
    if (dropDay === iso && !e.currentTarget.contains(e.relatedTarget)) dropDay = null
  }
  async function drop(e, iso) {
    e.preventDefault()
    dropDay = null
    const d = drag
    drag = null
    const c = byId(d ? d.id : Number(e.dataTransfer.getData('text/plain')))
    if (!c) return
    // Unscheduled (or a dated contact with no action text — the server needs both): ask for the action.
    if (!c.next_date || !c.next_action) return setNextDialog({ date: iso, contactId: c.id })
    if (c.next_date === iso) return
    await mutate('/api/contacts/next', { id: c.id, name: c.name, action: c.next_action, date: iso }, 'Moved ' + c.name + ' to ' + iso)
  }

  // Empty area of a day → schedule someone on that day. Chips, "+N more" and the
  // day number have their own handlers. Day cells themselves are layout, not
  // tab stops: the chips and day number inside are the focusable controls.
  function cellClick(e, iso) {
    if (e.target.closest('.cal-chip, .cal-more, .cal-daynum')) return
    setNextDialog({ date: iso })
  }
  function actKey(e, fn) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      fn()
    }
  }
  const done = (c) => mutate('/api/contacts/done', { id: c.id, name: c.name }, 'Done: ' + (c.next_action || c.name))

  let open = $state({ agenda: true, unsched: true })
</script>

{#if S.error}
  <ErrorBox message={"Couldn't load data: " + S.error} onretry={loadState} />
{:else if !S.loaded}
  <SkeletonRows />
{:else}
  <ViewHead title={monthTitle(ym)} sub={'Today is ' + S.today}>
    <div class="cal-nav">
      <button class="btn sm icon" title="Previous month ([)" aria-label="Previous month" onclick={() => calShift(-1)}><Icon name="prev" size={15} /></button>
      <button class="btn sm" title="Back to today (t)" onclick={calToday}>Today</button>
      <button class="btn sm icon" title="Next month (])" aria-label="Next month" onclick={() => calShift(1)}><Icon name="next" size={15} /></button>
    </div>
  </ViewHead>
  <FiltersBar shown={inMonth} noNext={false} label={inMonth + (inMonth === 1 ? ' next action' : ' next actions') + ' this month'} />

  <div class="cal-layout">
    <section class="cal-grid" aria-label={monthTitle(ym)}>
      <div class="cal-head cal-wk" title="ISO week">wk</div>
      {#each WEEKDAYS as w, i (w)}<div class="cal-head" class:weekend={i >= 5}>{w}</div>{/each}
      {#each weeks as wk (wk.days[0].iso)}
        <div class="cal-wk" title="ISO week {wk.week}">{wk.week}</div>
        {#each wk.days as d (d.iso)}
          {@const items = byDay.get(d.iso) || []}
          {@const extra = items.length - MAX_CHIPS}
          <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
          <div
            class="cal-day"
            data-date={d.iso}
            class:out={!d.inMonth}
            class:weekend={d.weekend}
            class:today={d.iso === S.today}
            class:past={d.iso < S.today}
            class:selected={d.iso === selected}
            class:drop={dropDay === d.iso}
            title={items.length ? undefined : 'Click to set a next action on ' + d.iso}
            onclick={(e) => cellClick(e, d.iso)}
            ondragover={(e) => dragover(e, d.iso)}
            ondragleave={(e) => dragleave(e, d.iso)}
            ondrop={(e) => drop(e, d.iso)}
          >
            <button type="button" class="cal-daynum" title="Show {d.iso} in the agenda" onclick={() => calSelect(d.iso)}>{d.day}</button>
            <div class="cal-chips">
              {#each items.slice(0, MAX_CHIPS) as c (c.id)}
                {@const t = stageStyle(c.stage)}
                <div
                  class="cal-chip tint {chipStatus(c)}"
                  class:dragging={drag?.id === c.id}
                  draggable="true"
                  tabindex="0"
                  role="button"
                  style:--h={t.h}
                  style:--s={t.sat}
                  title={chipTitle(c)}
                  onclick={() => openDrawer(c.id)}
                  onkeydown={(e) => actKey(e, () => openDrawer(c.id))}
                  ondragstart={(e) => dragstart(e, c, 'chip')}
                  ondragend={dragend}
                >
                  <div class="cal-chip-top">
                    <span class="cal-dot" title={c.stage}></span>
                    <span class="cal-chip-name">{c.name || '(no name)'}</span>
                    {#if c.company}<span class="cal-chip-co">{c.company}</span>{/if}
                  </div>
                  {#if c.next_action}<div class="cal-chip-act">{c.next_action}</div>{/if}
                </div>
              {/each}
              {#if extra > 0}
                <button type="button" class="cal-more" onclick={() => calSelect(d.iso)}>+{extra} more</button>
              {/if}
            </div>
          </div>
        {/each}
      {/each}
    </section>

    <aside class="cal-side">
      <section class="cal-panel cal-agenda">
        <h2>
          <button type="button" class="cal-toggle" aria-expanded={open.agenda} aria-label="Toggle agenda" onclick={() => (open.agenda = !open.agenda)}><Icon name="chevron" size={13} /></button>
          Agenda <span class="muted">{dateWithRel(selected)}</span>
          <span class="count-badge {agenda.some((c) => chipStatus(c) === 'overdue') ? 'danger' : ''}">{agenda.length}</span>
        </h2>
        {#if open.agenda}
          <div class="due-list">
            {#if !agenda.length}<div class="due-empty">Nothing due</div>{/if}
            {#each agenda as c (c.id)}
              <div class="cal-row">
                <div class="cal-row-main">
                  <button class="linkish name" onclick={() => openDrawer(c.id)}>{c.name || '(no name)'}</button>
                  {#if c.company}<span class="muted">{c.company}</span>{/if}
                  <StageChip stage={c.stage} small />
                </div>
                <div class="cal-row-act">
                  <span class="txt" class:muted={!c.next_action} title={c.next_action}>{c.next_action || '—'}</span>
                  <span class="date {dueStatus(c)}">{dateWithRel(c.next_date)}</span>
                </div>
                <div class="due-btns">
                  <Btn class="btn sm" title="Mark next action done" onclick={() => done(c)}><Icon name="check" size={14} />Done</Btn>
                  <button class="btn sm ghost" onclick={() => openDrawer(c.id)}><Icon name="open" size={14} />Open</button>
                </div>
              </div>
            {/each}
          </div>
        {/if}
      </section>

      <section class="cal-panel cal-unsched">
        <h2>
          <button type="button" class="cal-toggle" aria-expanded={open.unsched} aria-label="Toggle unscheduled" onclick={() => (open.unsched = !open.unsched)}><Icon name="chevron" size={13} /></button>
          Unscheduled <span class="muted">drag onto a day</span>
          <span class="count-badge">{unscheduled.length}</span>
        </h2>
        {#if open.unsched}
          <div class="due-list">
            {#if !unscheduled.length}<div class="due-empty">Every active contact has a next step.</div>{/if}
            {#each unscheduled as c (c.id)}
              <div
                class="cal-urow"
                class:dragging={drag?.id === c.id}
                draggable="true"
                tabindex="0"
                role="button"
                title="{c.name} — drag onto a day, or click to open"
                onclick={() => openDrawer(c.id)}
                onkeydown={(e) => actKey(e, () => openDrawer(c.id))}
                ondragstart={(e) => dragstart(e, c, 'unsched')}
                ondragend={dragend}
              >
                <Icon name="grip" size={14} />
                <span class="name">{c.name || '(no name)'}</span>
                <span class="muted">{subline(c)}</span>
                <StageChip stage={c.stage} small />
                <button class="btn sm ghost" title="Set next action on {selected}" onclick={(e) => { e.stopPropagation(); setNextDialog({ date: selected, contactId: c.id }) }}>
                  <Icon name="plus" size={13} />
                </button>
              </div>
            {/each}
          </div>
        {/if}
      </section>
    </aside>
  </div>
{/if}
