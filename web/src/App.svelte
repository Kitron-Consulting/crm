<script>
  import { onMount } from 'svelte'
  import Icon from './components/Icon.svelte'
  import Sidebar from './components/Sidebar.svelte'
  import Toasts from './components/Toasts.svelte'
  import Modal from './components/Modal.svelte'
  import Board from './views/Board.svelte'
  import Contacts from './views/Contacts.svelte'
  import Due from './views/Due.svelte'
  import Import from './views/Import.svelte'
  import Calendar from './views/Calendar.svelte'
  import Timeline from './views/Timeline.svelte'
  import Drawer from './drawer/Drawer.svelte'
  import AccountDrawer from './drawer/AccountDrawer.svelte'
  import { TOKEN } from './lib/api.js'
  import { UI, VIEWS, setView, loadState, clearSearch, closeDrawer, closeAccount, calShift, calToday } from './lib/store.svelte.js'
  import { modal, closeModal, newContactDialog } from './lib/modal.svelte.js'
  import { preview, closePreview } from './lib/preview.svelte.js'
  import AttachmentPreview from './components/AttachmentPreview.svelte'

  let searchEl = $state()

  onMount(() => {
    if (TOKEN) loadState()
  })

  // Debounce the search box into the value the views filter on.
  $effect(() => {
    const v = UI.qInput
    const t = setTimeout(() => (UI.q = v), 60)
    return () => clearTimeout(t)
  })

  function onkeydown(e) {
    const t = e.target
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
    if (e.key === 'Escape') {
      if (preview.item) return closePreview()
      if (modal.kind) return closeModal()
      if (UI.account.id !== null) return closeAccount()
      if (UI.drawer.id !== null) return closeDrawer()
      if (UI.q || UI.qInput) clearSearch()
      if (typing) t.blur()
      return
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return
    if (e.key === '/') {
      e.preventDefault()
      searchEl.focus()
      searchEl.select()
    } else if (e.key === 'n') {
      e.preventDefault()
      newContactDialog()
    } else if (e.key >= '1' && e.key <= '6') setView(VIEWS[Number(e.key) - 1])
    else if (UI.view === 'calendar') {
      // Month navigation only while the Calendar is showing.
      if (e.key === '[') calShift(-1)
      else if (e.key === ']') calShift(1)
      else if (e.key === 't') calToday()
    }
  }
</script>

<svelte:window {onkeydown} />

{#if !TOKEN}
  <div class="gate">
    <div class="card-box">
      <h1>No access token</h1>
      <p>This page is only reachable through the link <code>crm serve</code> prints in the terminal.</p>
      <p class="muted">Open that URL (it ends in <code>?t=…</code>) instead of this one.</p>
    </div>
  </div>
{:else}
  <div class="app">
    <Sidebar />
    <div class="main" class:fill={UI.view === 'board'}>
      <header class="topbar">
        <div class="search">
          <Icon name="search" size={15} />
          <input
            bind:this={searchEl}
            class="ctl"
            type="search"
            placeholder="Search name, company, email, role, stage…"
            autocomplete="off"
            spellcheck="false"
            bind:value={UI.qInput}
          />
          <span class="kbd-hint"><kbd>/</kbd></span>
          <button
            class="btn ghost sm icon clear"
            title="Clear search"
            hidden={!UI.qInput}
            onclick={() => {
              clearSearch()
              searchEl.focus()
            }}><Icon name="close" size={14} /></button
          >
        </div>
        <div class="spacer"></div>
        <button class="btn primary" title="New contact (n)" onclick={newContactDialog}><Icon name="plus" size={15} />New contact</button>
      </header>
      <main>
        {#if UI.view === 'board'}
          <Board />
        {:else if UI.view === 'contacts'}
          <Contacts />
        {:else if UI.view === 'due'}
          <Due />
        {:else if UI.view === 'import'}
          <Import />
        {:else if UI.view === 'calendar'}
          <Calendar />
        {:else if UI.view === 'timeline'}
          <Timeline />
        {/if}
      </main>
    </div>
  </div>
  <Drawer />
  <AccountDrawer />
  {#if modal.kind}<Modal />{/if}
  {#if preview.item}<AttachmentPreview />{/if}
  <Toasts />
{/if}
