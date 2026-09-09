<script>
  // Inline "set next action" form (Due rows + drawer). Both fields are required
  // by the server; the date may be absolute or relative (+7d).
  import { mutate } from '../lib/store.svelte.js'
  let { contact: c, oncancel } = $props()
  // The form is mounted fresh each time editing starts, so seeding the
  // inputs from the contact's current values (once) is intended.
  // svelte-ignore state_referenced_locally
  let action = $state(c.next_action || '')
  // svelte-ignore state_referenced_locally
  let date = $state(c.next_date || '')
  let busy = $state(false)
  let actionEl = $state()
  $effect(() => actionEl?.focus())

  async function submit(e) {
    e.preventDefault()
    const a = action.trim()
    if (!a) {
      actionEl.focus()
      return
    }
    busy = true
    const r = await mutate(
      '/api/contacts/next',
      { id: c.id, name: c.name, action: a, date: date.trim() },
      'Next action set',
    ).finally(() => (busy = false))
    if (r) oncancel()
  }

  function onkeydown(e) {
    if (e.key === 'Escape') {
      e.stopPropagation()
      oncancel()
    }
  }
</script>

<!-- Esc inside the form cancels the edit instead of closing the drawer. -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<form class="next-form" onsubmit={submit} {onkeydown}>
  <input bind:this={actionEl} class="ctl" placeholder="Next action, e.g. Follow up on proposal" required bind:value={action} />
  <input class="ctl" placeholder="YYYY-MM-DD or +7d" bind:value={date} />
  <button class="btn sm primary" class:busy type="submit" disabled={busy}>Save</button>
  <button class="btn sm ghost" type="button" onclick={oncancel}>Cancel</button>
  <div class="hint">Date accepts an absolute date (2026-09-16) or a relative one like +3d, +7d, +30d.</div>
</form>
