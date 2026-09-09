<script>
  import Icon from './Icon.svelte'
  import { S, UI, VIEWS, STUCK_DAYS, setView, dueStatus, calMonth, isStuck } from '../lib/store.svelte.js'
  import { IMP } from '../lib/import.svelte.js'
  import { monthTitle } from '../lib/cal.js'

  const overdue = $derived(S.contacts.filter((c) => dueStatus(c) === 'overdue').length)
  const soon = $derived(S.contacts.filter((c) => dueStatus(c) === 'soon').length)
  const n = $derived(S.loaded && S.contacts.length ? String(S.contacts.length) : '')

  // Calendar badge: next actions in the month the calendar is showing; red if any are overdue.
  const calItems = $derived(S.today ? S.contacts.filter((c) => c.next_date && c.next_date.slice(0, 7) === calMonth()) : [])
  const calOverdue = $derived(calItems.filter((c) => c.next_date < S.today).length)

  // Timeline badge: open contacts sitting in their current stage ≥ 30 days.
  const stuck = $derived(S.today ? S.contacts.filter(isStuck).length : 0)

  // Sidebar order (Calendar and Timeline between Due and Import) differs from
  // VIEWS order, which fixes the number shortcuts — so the title reads the real key.
  const items = $derived([
    { view: 'board', label: 'Board', badge: n, cls: '', title: '' },
    { view: 'contacts', label: 'Contacts', badge: n, cls: '', title: '' },
    {
      view: 'accounts',
      label: 'Accounts',
      badge: S.accounts.length ? String(S.accounts.length) : '',
      cls: '', title: '',
    },
    {
      view: 'due',
      label: 'Due',
      badge: overdue ? String(overdue) : soon ? String(soon) : '',
      cls: overdue ? 'danger' : soon ? 'warn' : '',
      title: overdue ? overdue + ' overdue' : soon ? soon + ' due this week' : '',
    },
    {
      view: 'calendar',
      label: 'Calendar',
      badge: calItems.length ? String(calItems.length) : '',
      cls: calOverdue ? 'danger' : '',
      title: calItems.length
        ? calItems.length + ' next action' + (calItems.length === 1 ? '' : 's') + ' in ' + monthTitle(calMonth()) +
          (calOverdue ? ' (' + calOverdue + ' overdue)' : '')
        : '',
    },
    {
      view: 'timeline',
      label: 'Timeline',
      badge: stuck ? String(stuck) : '',
      cls: stuck ? 'danger' : '',
      title: stuck ? stuck + ' stuck in stage ≥ ' + STUCK_DAYS + ' days' : '',
    },
    { view: 'import', label: 'Import', badge: IMP.rows.length ? String(IMP.rows.length) : '', cls: '', title: '' },
  ])

  // "(3) crm" in the tab title while anything is overdue.
  $effect(() => {
    document.title = overdue ? '(' + overdue + ') crm' : 'crm'
  })
</script>

<aside class="sidebar">
  <div class="brand"><span class="logo">crm</span><span class="word">Pipeline</span></div>
  <nav>
    {#each items as it (it.view)}
      <button class="nav-btn" class:active={UI.view === it.view} title="{it.label} ({VIEWS.indexOf(it.view) + 1})" onclick={() => setView(it.view)}>
        <Icon name={it.view} size={16} />
        <span class="lbl">{it.label}</span>
        <span class="badge {it.cls}" title={it.title || undefined}>{it.badge}</span>
      </button>
    {/each}
  </nav>
  <div class="sidebar-foot">
    <div><span>Search</span><kbd>/</kbd></div>
    <div><span>New contact</span><kbd>n</kbd></div>
    <div><span>Switch view</span><kbd>1–7</kbd></div>
    {#if UI.view === 'calendar'}
      <div><span>Month</span><kbd>[</kbd> <kbd>]</kbd> <kbd>t</kbd></div>
    {/if}
    <div><span>Close</span><kbd>esc</kbd></div>
  </div>
</aside>
