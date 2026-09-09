<script>
  // Kanban by stage. HTML5 drag-and-drop between columns updates the stage.
  import ViewHead from '../components/ViewHead.svelte'
  import FiltersBar from '../components/FiltersBar.svelte'
  import StageChip from '../components/StageChip.svelte'
  import NextChip from '../components/NextChip.svelte'
  import MeetingChip from '../components/MeetingChip.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import ErrorBox from '../components/ErrorBox.svelte'
  import Icon from '../components/Icon.svelte'
  import { S, visibleContacts, byId, mutate, openDrawer, setView, loadState, stageStyle, subline, refreshMeetings } from '../lib/store.svelte.js'
  import { newContactDialog } from '../lib/modal.svelte.js'

  let syncing = $state(false)
  async function syncMeetings() {
    syncing = true
    try {
      await refreshMeetings()
    } finally {
      syncing = false
    }
  }

  let dragId = $state(null)
  let dropStage = $state(null)

  const list = $derived(visibleContacts())
  const columns = $derived.by(() => {
    const m = new Map(S.stages.map((s) => [s, []]))
    for (const c of list) {
      if (!c.stage) continue // no stage = not in the pipeline; shown in Contacts, not the board
      if (!m.has(c.stage)) m.set(c.stage, [])
      m.get(c.stage).push(c)
    }
    return [...m].map(([stage, cards]) => ({ stage, cards, ...stageStyle(stage) }))
  })
  const staged = $derived(columns.reduce((n, col) => n + col.cards.length, 0))

  $effect(() => {
    document.body.classList.toggle('dragging-any', dragId !== null)
    return () => document.body.classList.remove('dragging-any')
  })

  function dragstart(e, c) {
    dragId = c.id
    e.dataTransfer.setData('text/plain', String(c.id))
    e.dataTransfer.effectAllowed = 'move'
  }
  function dragend() {
    dragId = null
    dropStage = null
  }
  function dragover(e, stage) {
    if (dragId === null) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    dropStage = stage
  }
  function dragleave(e, stage) {
    if (dropStage === stage && !e.currentTarget.contains(e.relatedTarget)) dropStage = null
  }
  async function drop(e, stage) {
    e.preventDefault()
    dropStage = null
    const c = byId(Number(e.dataTransfer.getData('text/plain')))
    dragId = null
    if (!c || c.stage === stage) return
    await mutate('/api/contacts/update', { id: c.id, name: c.name, fields: { stage } }, (c.name || 'Contact') + ' → ' + stage)
  }
  function cardKey(e, id) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      openDrawer(id)
    }
  }
</script>

{#if S.error}
  <ErrorBox message={"Couldn't load data: " + S.error} onretry={loadState} />
{:else if !S.loaded}
  <div class="board">
    {#each [3, 2, 3, 2] as n}
      <div class="col">
        <div class="col-head"><div class="sk" style="width:80px;height:20px"></div><div class="sk" style="width:20px;height:14px"></div></div>
        <div class="col-body">
          {#each { length: n } as _}<div class="sk" style="width:100%;height:64px"></div>{/each}
        </div>
      </div>
    {/each}
  </div>
{:else}
  <ViewHead title="Board" sub={S.today ? 'Today is ' + S.today : ''}>
    <button class="btn sm" class:busy={syncing} disabled={syncing} onclick={syncMeetings}
            title={S.meetingsFetchedAt ? 'Meetings last synced ' + S.meetingsFetchedAt : 'Scan mail for upcoming meetings'}>
      <Icon name="calendar" size={14} />{syncing ? 'Syncing…' : 'Sync meetings'}
    </button>
  </ViewHead>
  <FiltersBar shown={staged} />
  {#if !S.contacts.length}
    <EmptyState title="No contacts yet" hint="Add your first contact, or import people you've emailed.">
      <div>
        <button class="btn primary" onclick={newContactDialog}><Icon name="plus" size={14} />New contact</button>
        {' '}
        <button class="btn" onclick={() => setView('import')}>Import from mail</button>
      </div>
    </EmptyState>
  {:else}
    <div class="board">
      {#each columns as col (col.stage)}
        <section
          class="col tint"
          class:drop={dropStage === col.stage}
          role="group"
          aria-label={col.stage}
          style:--h={col.h}
          style:--s={col.sat}
          ondragover={(e) => dragover(e, col.stage)}
          ondragleave={(e) => dragleave(e, col.stage)}
          ondrop={(e) => drop(e, col.stage)}
        >
          <header class="col-head"><StageChip stage={col.stage} /><span class="col-count">{col.cards.length}</span></header>
          <div class="col-body">
            {#if !col.cards.length}
              <div class="col-empty">{dragId !== null ? 'Drop here' : 'Empty'}</div>
            {/if}
            {#each col.cards as c (c.id)}
              {@const sub = subline(c) || c.email || ''}
              <div
                class="card"
                class:dragging={dragId === c.id}
                draggable="true"
                tabindex="0"
                role="button"
                onclick={() => openDrawer(c.id)}
                onkeydown={(e) => cardKey(e, c.id)}
                ondragstart={(e) => dragstart(e, c)}
                ondragend={dragend}
              >
                <div class="card-name" title={c.name}>{c.name || '(no name)'}</div>
                {#if sub}<div class="card-sub" title={sub}>{sub}</div>{/if}
                {#if c.next_action || c.next_date || c.next_meeting}
                  <div class="card-foot">
                    {#if c.next_action || c.next_date}<NextChip contact={c} />{/if}
                    <MeetingChip contact={c} />
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        </section>
      {/each}
    </div>
  {/if}
{/if}
