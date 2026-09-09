<script>
  import Select from './Select.svelte'
  import { post } from '../lib/api.js'
  import { S, loadState, openDrawer } from '../lib/store.svelte.js'
  import { closeModal } from '../lib/modal.svelte.js'
  import { toast } from '../lib/toast.svelte.js'

  const stages = S.stages.length ? S.stages : ['lead']
  const sources = S.sources.length ? S.sources : ['cold']
  let f = $state({
    name: '',
    email: '',
    phone: '',
    company: '',
    role: '',
    stage: stages[0],
    source: sources.includes('cold') ? 'cold' : sources[0],
  })
  let err = $state('')
  let busy = $state(false)
  let nameEl = $state()
  $effect(() => nameEl?.focus())

  async function submit(e) {
    e.preventDefault()
    err = ''
    const fields = {}
    for (const k of Object.keys(f)) fields[k] = (f[k] || '').trim()
    if (!fields.name) {
      err = 'Name is required.'
      nameEl.focus()
      return
    }
    busy = true
    try {
      const r = await post('/api/contacts/add', { fields })
      closeModal()
      toast('Added ' + fields.name, 'ok')
      await loadState()
      if (r.contact && r.contact.id !== undefined) openDrawer(r.contact.id)
    } catch (ex) {
      err = ex.message
    } finally {
      busy = false
    }
  }
</script>

<form onsubmit={submit}>
  <h2>New contact</h2>
  <p class="lead">Only the name is required; everything else can be filled in later.</p>
  <div class="form-grid">
    <label class="f full">Name<input bind:this={nameEl} class="ctl" type="text" autocomplete="off" required bind:value={f.name} /></label>
    <label class="f">Email<input class="ctl" type="email" autocomplete="off" bind:value={f.email} /></label>
    <label class="f">Phone<input class="ctl" type="tel" autocomplete="off" bind:value={f.phone} /></label>
    <label class="f">Company<input class="ctl" type="text" autocomplete="off" bind:value={f.company} /></label>
    <label class="f">Role<input class="ctl" type="text" autocomplete="off" bind:value={f.role} /></label>
    <label class="f">Stage<Select options={stages} bind:value={f.stage} /></label>
    <label class="f">Source<Select options={sources} bind:value={f.source} /></label>
  </div>
  <div class="form-error">{err}</div>
  <div class="modal-actions">
    <button class="btn" type="button" onclick={closeModal}>Cancel</button>
    <button class="btn primary" class:busy type="submit" disabled={busy}>Add contact</button>
  </div>
</form>
