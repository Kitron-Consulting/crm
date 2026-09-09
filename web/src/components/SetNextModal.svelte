<script>
  // Calendar "Set next action" dialog: pick a contact (unscheduled ones first),
  // an action and a date. Opened from an empty day cell (date prefilled) or by
  // dropping an Unscheduled contact on a day (contact + date prefilled).
  import Icon from './Icon.svelte'
  import StageChip from './StageChip.svelte'
  import { S, CLOSED, byId, mutate, cmpStr, hasNext, subline } from '../lib/store.svelte.js'
  import { closeModal } from '../lib/modal.svelte.js'

  let { date: date0 = '', contactId = null } = $props()
  // Mounted fresh per open, so seeding once from the props is intended.
  // svelte-ignore state_referenced_locally
  let date = $state(date0)
  // svelte-ignore state_referenced_locally
  let picked = $state(contactId)
  let q = $state('')
  let action = $state('')
  let err = $state('')
  let busy = $state(false)
  let qEl = $state()
  let actionEl = $state()
  // Focus the picker until a contact is chosen, then the action field.
  $effect(() => { (picked === null ? qEl : actionEl)?.focus() })

  const contact = $derived(picked === null ? null : byId(picked))
  const isOpen = (c) => !CLOSED.has((c.stage || '').toLowerCase())
  // Unscheduled + open first, then unscheduled closed, then everyone who already has a next step.
  const rank = (c) => (!hasNext(c) ? (isOpen(c) ? 0 : 1) : 2)
  const candidates = $derived.by(() => {
    const s = q.trim().toLowerCase()
    return S.contacts
      .filter((c) => !s || [c.name, c.company].some((v) => (v || '').toLowerCase().includes(s)))
      .sort((a, b) => rank(a) - rank(b) || cmpStr(a.name, b.name))
      .slice(0, 50)
  })

  function pickKey(e) {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (candidates.length) picked = candidates[0].id
    }
  }

  async function submit(e) {
    e.preventDefault()
    err = ''
    if (!contact) { err = 'Pick a contact.'; qEl?.focus(); return }
    const a = action.trim()
    if (!a) { err = 'Action is required.'; actionEl?.focus(); return }
    if (!date.trim()) { err = 'Date is required.'; return }
    busy = true
    const r = await mutate(
      '/api/contacts/next',
      { id: contact.id, name: contact.name, action: a, date: date.trim() },
      'Next action set for ' + contact.name,
    ).finally(() => (busy = false))
    if (r) closeModal()
  }
</script>

<form class="setnext" onsubmit={submit}>
  <h2>Set next action</h2>
  <p class="lead">Pick a contact, say what happens next and when.</p>

  <div class="f pick">
    <span>Contact</span>
    {#if contact}
      <div class="picked">
        <strong>{contact.name || '(no name)'}</strong>
        {#if subline(contact)}<span class="muted">{subline(contact)}</span>{/if}
        <StageChip stage={contact.stage} small />
        <button type="button" class="btn sm ghost" onclick={() => { picked = null; q = '' }}>Change</button>
      </div>
    {:else}
      <input bind:this={qEl} class="ctl" placeholder="Search name or company…" autocomplete="off" bind:value={q} onkeydown={pickKey} />
      <div class="pick-list" role="listbox" aria-label="Contacts">
        {#each candidates as c (c.id)}
          <button type="button" class="pick-row" role="option" aria-selected="false" onclick={() => (picked = c.id)}>
            <span class="pick-name">{c.name || '(no name)'}</span>
            <span class="pick-sub muted">{subline(c)}</span>
            {#if hasNext(c)}
              <span class="pick-has" title={(c.next_action || '') + (c.next_date ? ' — ' + c.next_date : '')}>
                <Icon name="clock" size={11} />{c.next_date || 'has next'}
              </span>
            {:else}
              <span class="pick-free">unscheduled</span>
            {/if}
          </button>
        {:else}
          <div class="pick-empty muted">No contacts match.</div>
        {/each}
      </div>
    {/if}
  </div>

  <div class="form-grid">
    <label class="f full">Action<input bind:this={actionEl} class="ctl" placeholder="e.g. Follow up on proposal" autocomplete="off" bind:value={action} /></label>
    <label class="f full">Date<input class="ctl" placeholder="YYYY-MM-DD or +7d" autocomplete="off" bind:value={date} /></label>
  </div>
  <div class="form-error">{err}</div>
  <div class="modal-actions">
    <button class="btn" type="button" onclick={closeModal}>Cancel</button>
    <button class="btn primary" class:busy type="submit" disabled={busy}>Save</button>
  </div>
</form>
