<script>
  // Account detail panel — a right slide-over (mirrors the contact Drawer).
  // Editable name/domain/notes; renaming cascades to linked contacts server-side.
  import Icon from '../components/Icon.svelte'
  import StageChip from '../components/StageChip.svelte'
  import {
    S, UI, accountById, contactsInAccount, closeAccount, openDrawer, updateAccount,
  } from '../lib/store.svelte.js'

  const open = $derived(UI.account.id !== null)
  const account = $derived(open ? accountById(UI.account.id) : undefined)
  // Keep the last account rendered while the panel slides out.
  let shown = $state(null)
  $effect(() => {
    if (account) shown = account
  })
  // The account vanished (renamed/merged elsewhere): close.
  $effect(() => {
    if (open && S.loaded && !account) closeAccount()
  })

  // Editable draft, reset whenever a different account is shown.
  let draft = $state({ name: '', domain: '', notes: '' })
  let dirty = $state(false)
  let busy = $state(false)
  let lastId = $state(null)
  $effect(() => {
    if (shown && shown.id !== lastId) {
      lastId = shown.id
      draft = { name: shown.name, domain: shown.domain || '', notes: shown.notes || '' }
      dirty = false
    }
  })
  const contacts = $derived(shown ? contactsInAccount(shown.id) : [])

  function edit(k, v) {
    draft[k] = v
    dirty = true
  }
  async function save() {
    if (!draft.name.trim()) return
    busy = true
    const r = await updateAccount(shown.id, draft)
    busy = false
    if (r) dirty = false // rename clash / error is toasted by mutate(); keep the draft
  }
  function openContact(id) {
    closeAccount()
    openDrawer(id)
  }

  let drawerEl = $state()
  $effect(() => {
    if (UI.account.id !== null) drawerEl?.focus({ preventScroll: true })
  })
</script>

<div class="scrim" class:show={open} onclick={closeAccount} aria-hidden="true"></div>
<aside class="drawer" class:open bind:this={drawerEl} tabindex="-1" aria-label="Account details">
  {#if shown}
    <header class="dr-head">
      <div style="min-width:0">
        <div class="sub">Account</div>
        <h2>{shown.name}</h2>
      </div>
      <button class="btn ghost icon" title="Close (Esc)" onclick={closeAccount}><Icon name="close" size={16} /></button>
    </header>
    <div class="dr-body">
      <section class="acc-fields">
        <label class="f"><span>Name</span>
          <input class="ctl" value={draft.name} oninput={(e) => edit('name', e.target.value)} /></label>
        <label class="f"><span>Domain</span>
          <input class="ctl" placeholder="acme.fi" value={draft.domain} oninput={(e) => edit('domain', e.target.value)} /></label>
        <label class="f"><span>Notes</span>
          <textarea class="ctl acc-notes" rows="4" value={draft.notes} oninput={(e) => edit('notes', e.target.value)}></textarea></label>
        {#if dirty}
          <div class="acc-save">
            <button type="button" class="btn primary sm" class:busy disabled={busy} onclick={save}>Save account</button>
          </div>
        {/if}
      </section>

      <section>
        <div class="block-title">Contacts <span class="count-badge">{contacts.length}</span></div>
        <div class="acc-contacts">
          {#if !contacts.length}<div class="due-empty">No linked contacts.</div>{/if}
          {#each contacts as c (c.id)}
            <button type="button" class="acc-row" onclick={() => openContact(c.id)}>
              <span class="name">{c.name || '(no name)'}</span>
              {#if c.role}<span class="muted">{c.role}</span>{/if}
              <StageChip stage={c.stage} small />
            </button>
          {/each}
        </div>
      </section>
    </div>
  {/if}
</aside>
