<script>
  // Editable contact fields with dirty tracking. Only changed keys are sent.
  import Icon from '../components/Icon.svelte'
  import Select from '../components/Select.svelte'
  import { S, mutate } from '../lib/store.svelte.js'
  import { toast } from '../lib/toast.svelte.js'

  let { contact: c } = $props()
  const FIELDS = [
    ['name', 'Name', 'text'],
    ['email', 'Email', 'email'],
    ['phone', 'Phone', 'tel'],
    ['company', 'Company', 'text'],
    ['role', 'Role', 'text'],
  ]

  // draft holds only the keys the user has touched; everything else reads from the contact.
  let draft = $state({})
  let busy = $state(false)
  let nameEl = $state()
  const val = (k) => (k in draft ? draft[k] : c[k] || '')
  const dirty = $derived([...FIELDS.map((f) => f[0]), 'source'].some((k) => val(k) !== (c[k] || '')))

  async function submit(e) {
    e.preventDefault()
    const fields = {}
    for (const k of [...FIELDS.map((f) => f[0]), 'source']) {
      const nv = val(k).trim()
      if (nv !== (c[k] || '')) fields[k] = nv
    }
    if (!Object.keys(fields).length) return
    if (fields.name === '') {
      toast("Name can't be empty.", 'error')
      nameEl.focus()
      return
    }
    busy = true
    const r = await mutate('/api/contacts/update', { id: c.id, name: c.name, fields }, 'Saved').finally(() => (busy = false))
    if (r) draft = {}
  }
</script>

<form class="block" onsubmit={submit}>
  <div class="block-title"><span>Details</span></div>
  <div class="fields">
    {#each FIELDS as [k, label, type] (k)}
      <label class="field-label" for="f-{k}">{label}</label>
      {#if k === 'email'}
        <div class="field-inline">
          <input id="f-{k}" class="ctl" {type} value={val(k)} placeholder="—" autocomplete="off" oninput={(e) => (draft[k] = e.target.value)} />
          {#if c.email}
            <a class="btn ghost icon" href="mailto:{c.email}" title="Email {c.email}"><Icon name="mail" size={15} /></a>
          {/if}
        </div>
      {:else if k === 'name'}
        <input id="f-{k}" bind:this={nameEl} class="ctl" {type} value={val(k)} placeholder="—" autocomplete="off" oninput={(e) => (draft[k] = e.target.value)} />
      {:else}
        <input id="f-{k}" class="ctl" {type} value={val(k)} placeholder="—" autocomplete="off" oninput={(e) => (draft[k] = e.target.value)} />
      {/if}
    {/each}
    <label class="field-label" for="f-source">Source</label>
    <Select id="f-source" options={S.sources} value={val('source')} onchange={(v) => (draft.source = v)} />
  </div>
  <div class="field-actions" hidden={!dirty}>
    <button class="btn ghost sm" type="button" onclick={() => (draft = {})}>Discard</button>
    <button class="btn primary sm" class:busy type="submit" disabled={busy}>Save changes</button>
  </div>
</form>
