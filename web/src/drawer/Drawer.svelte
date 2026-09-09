<script>
  // Right-hand contact panel. Always in the DOM (CSS slides it in/out);
  // content is rendered while a contact is selected.
  import Icon from '../components/Icon.svelte'
  import Btn from '../components/Btn.svelte'
  import NextBlock from './NextBlock.svelte'
  import FieldsBlock from './FieldsBlock.svelte'
  import NotesBlock from './NotesBlock.svelte'
  import ThreadBlock from './ThreadBlock.svelte'
  import { S, UI, byId, closeDrawer, mutate, subline, stageStyle, meetingWhen } from '../lib/store.svelte.js'
  import { confirmDialog } from '../lib/modal.svelte.js'

  const open = $derived(UI.drawer.id !== null)
  const contact = $derived(open ? byId(UI.drawer.id) : undefined)
  // Keep the last contact rendered while the panel slides out.
  let shown = $state(null)
  $effect(() => {
    if (contact) shown = contact
  })
  // The selected contact vanished (removed elsewhere, list shrank): close.
  $effect(() => {
    if (open && S.loaded && !contact) closeDrawer()
  })
  let drawerEl = $state()
  $effect(() => {
    if (UI.drawer.id !== null) drawerEl?.focus({ preventScroll: true })
  })

  const tint = $derived(stageStyle(shown?.stage))
  const stageOptions = $derived(
    shown && shown.stage && !S.stages.includes(shown.stage) ? [shown.stage, ...S.stages] : S.stages,
  )

  async function changeStage(e, c) {
    const v = e.target.value
    if (v === c.stage) return
    const r = await mutate('/api/contacts/update', { id: c.id, name: c.name, fields: { stage: v } }, 'Stage → ' + v)
    if (!r) e.target.value = c.stage
  }

  async function remove(c) {
    const ok = await confirmDialog({
      title: 'Remove ' + (c.name || 'this contact') + '?',
      body: 'The contact is soft-deleted: it disappears from the pipeline but stays in the data file.',
      ok: 'Remove',
      danger: true,
    })
    if (!ok) return
    const r = await mutate('/api/contacts/remove', { id: c.id, name: c.name }, 'Removed ' + (c.name || 'contact'))
    if (r) closeDrawer()
  }
</script>

<div class="scrim" class:show={open} onclick={closeDrawer} aria-hidden="true"></div>
<aside class="drawer" class:open bind:this={drawerEl} tabindex="-1" aria-label="Contact details">
  {#if shown}
    {@const c = shown}
    <header class="dr-head">
      <div style="min-width:0">
        <h2>{c.name || '(no name)'}</h2>
        <div class="sub">{subline(c) || c.email || ''}</div>
      </div>
      <div class="dr-head-ctl">
        <span class="stage-select tint" style:--h={tint.h} style:--s={tint.sat}>
          <select value={c.stage} onchange={(e) => changeStage(e, c)} aria-label="Stage">
            {#each stageOptions as s (s)}<option value={s}>{s}</option>{/each}
          </select>
          <Icon name="chevron" size={12} />
        </span>
        <button class="btn ghost icon" title="Close (Esc)" onclick={closeDrawer}><Icon name="close" size={16} /></button>
      </div>
    </header>
    <div class="tabs">
      <button class="tab" class:active={UI.drawer.tab === 'details'} onclick={() => (UI.drawer.tab = 'details')}>Details</button>
      <button class="tab" class:active={UI.drawer.tab === 'thread'} onclick={() => (UI.drawer.tab = 'thread')}>Thread</button>
    </div>
    <div class="dr-body">
      {#key c.id}
        {#if UI.drawer.tab === 'thread'}
          <ThreadBlock contact={c} />
        {:else}
          <NextBlock contact={c} />
          {#if c.next_meeting}
            {@const w = meetingWhen(c.next_meeting)}
            <section class="dr-meeting">
              <div class="dr-meeting-head"><Icon name="calendar" size={13} />Next meeting</div>
              <div class="dr-meeting-title">{c.next_meeting.summary || 'Meeting'}</div>
              <div class="dr-meeting-when">{w.day}{w.time ? ' · ' + w.time : ''}{w.rel ? ' · ' + w.rel : ''}</div>
              {#if c.next_meeting.location}<div class="muted" style="font-size:12px">{c.next_meeting.location}</div>{/if}
              {#if c.next_meeting.join_url}
                <a class="btn sm" href={c.next_meeting.join_url} target="_blank" rel="noopener noreferrer">
                  <Icon name="open" size={13} />Join{c.next_meeting.provider ? ' ' + c.next_meeting.provider : ''}
                </a>
              {/if}
            </section>
          {/if}
          <FieldsBlock contact={c} />
          <NotesBlock contact={c} />
        {/if}
      {/key}
    </div>
    <footer class="dr-foot">
      <span class="muted" style="font-size:12px">{c.source ? 'Source: ' + c.source : ''}</span>
      <Btn class="btn ghost danger sm" onclick={() => remove(c)}><Icon name="trash" size={14} />Remove</Btn>
    </footer>
  {/if}
</aside>
