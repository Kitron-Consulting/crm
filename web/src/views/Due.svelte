<script>
  import ViewHead from '../components/ViewHead.svelte'
  import StageChip from '../components/StageChip.svelte'
  import ErrorBox from '../components/ErrorBox.svelte'
  import SkeletonRows from '../components/SkeletonRows.svelte'
  import NextForm from '../components/NextForm.svelte'
  import Btn from '../components/Btn.svelte'
  import Icon from '../components/Icon.svelte'
  import {
    S, UI, CLOSED, matchesSearch, dueStatus, hasNext, cmpStr, subline, dateWithRel, openDrawer, mutate, loadState,
  } from '../lib/store.svelte.js'

  // id of the row whose inline "set next" form is open
  let openForm = $state(null)

  const byDate = (a, b) => (a.next_date || '').localeCompare(b.next_date || '') || cmpStr(a.name, b.name)
  const list = $derived(S.contacts.filter(matchesSearch))
  const groups = $derived([
    { kind: 'overdue', title: 'Overdue', empty: 'Nothing overdue.', badge: 'danger',
      items: list.filter((c) => dueStatus(c) === 'overdue').sort(byDate) },
    { kind: 'soon', title: 'Due in the next 7 days', empty: 'Nothing due this week.', badge: 'warn',
      items: list.filter((c) => dueStatus(c) === 'soon').sort(byDate) },
    { kind: 'none', title: 'No next action', badge: '',
      empty: 'Every active contact has a next step. (Won, lost and dormant are excluded.)',
      items: list.filter((c) => !hasNext(c) && !CLOSED.has((c.stage || '').toLowerCase())).sort((a, b) => cmpStr(a.name, b.name)) },
  ])

  const done = (c) => mutate('/api/contacts/done', { id: c.id, name: c.name }, 'Done: ' + (c.next_action || c.name))
</script>

{#if S.error}
  <ErrorBox message={"Couldn't load data: " + S.error} onretry={loadState} />
{:else if !S.loaded}
  <SkeletonRows />
{:else}
  <ViewHead title="Due" sub={'Today is ' + S.today + (UI.q ? ' · filtered by search' : '')} />
  {#each groups as g (g.kind)}
    <section class="due-group {g.kind}">
      <h2>
        <span class="dot"></span>{g.title}
        <span class="count-badge {g.items.length ? g.badge : ''}">{g.items.length}</span>
      </h2>
      <div class="due-list">
        {#if !g.items.length}<div class="due-empty">{g.empty}</div>{/if}
        {#each g.items as c (c.id)}
          {@const st = dueStatus(c)}
          <div class="due-row">
            <div class="due-main">
              <button class="linkish name" onclick={() => openDrawer(c.id)}>{c.name || '(no name)'}</button>
              {#if subline(c)}<span class="muted">{subline(c)}</span>{/if}
              <StageChip stage={c.stage} small />
            </div>
            <div class="due-action">
              <span class="txt" class:muted={!c.next_action} title={c.next_action}>{c.next_action || '—'}</span>
              {#if c.next_date}<span class="date {st}">{dateWithRel(c.next_date)}</span>{/if}
            </div>
            <div class="due-btns">
              {#if hasNext(c)}
                <Btn class="btn sm" title="Mark next action done" onclick={() => done(c)}><Icon name="check" size={14} />Done</Btn>
              {/if}
              <button class="btn sm ghost" onclick={() => (openForm = openForm === c.id ? null : c.id)}>
                <Icon name="edit" size={13} />{hasNext(c) ? 'Edit' : 'Set next'}
              </button>
              <button class="btn sm ghost icon" title="Open" onclick={() => openDrawer(c.id)}><Icon name="open" size={14} /></button>
            </div>
            {#if openForm === c.id}
              <div class="due-form"><NextForm contact={c} oncancel={() => (openForm = null)} /></div>
            {/if}
          </div>
        {/each}
      </div>
    </section>
  {/each}
{/if}
