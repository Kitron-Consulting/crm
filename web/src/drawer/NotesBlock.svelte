<script>
  import { mutate } from '../lib/store.svelte.js'
  let { contact: c } = $props()
  const notes = $derived(c.notes || [])
  let text = $state('')
  let busy = $state(false)
  let ta = $state()

  async function submit() {
    const t = text.trim()
    if (!t) {
      ta.focus()
      return
    }
    busy = true
    const r = await mutate('/api/contacts/note', { id: c.id, name: c.name, text: t }, 'Note added').finally(() => (busy = false))
    if (r) text = ''
  }
  function onkeydown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
  }
</script>

<section class="block">
  <div class="block-title"><span>Notes</span><span class="count-badge">{notes.length}</span></div>
  <div class="note-compose">
    <textarea bind:this={ta} class="ctl" rows="3" placeholder="Add a note… (Ctrl/⌘+Enter to save)" bind:value={text} {onkeydown}></textarea>
    <div class="row-end">
      <button class="btn primary sm" class:busy type="button" disabled={busy} onclick={submit}>Add note</button>
    </div>
  </div>
  <ol class="timeline">
    {#each notes as n}
      <li><time>{n.date || ''}</time><p>{n.text || ''}</p></li>
    {:else}
      <li class="muted" style="font-size:13px">No notes yet.</li>
    {/each}
  </ol>
</section>
