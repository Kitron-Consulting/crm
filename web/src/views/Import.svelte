<script>
  import ViewHead from '../components/ViewHead.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import ErrorBox from '../components/ErrorBox.svelte'
  import Loading from '../components/Loading.svelte'
  import Select from '../components/Select.svelte'
  import Btn from '../components/Btn.svelte'
  import Icon from '../components/Icon.svelte'
  import { S } from '../lib/store.svelte.js'
  import { IMP, visibleRows, scan, setAll, commit } from '../lib/import.svelte.js'

  const stages = $derived(S.stages.length ? S.stages : ['contacted'])
  const sources = $derived(S.sources.length ? S.sources : ['cold'])
  const vis = $derived(visibleRows())
  const picked = $derived(vis.filter((r) => r.add).length)
  const countText = $derived(
    picked + ' of ' + vis.length + ' selected' + (vis.length !== IMP.rows.length ? ' (' + IMP.rows.length + ' total)' : ''),
  )
</script>

<ViewHead title="Import" sub="People you've emailed who aren't contacts yet, scanned from your Sent folder over IMAP." />
<div class="import-toolbar">
  <span>Last</span>
  <input
    class="ctl"
    type="number"
    min="1"
    placeholder="all"
    style="width:84px"
    bind:value={IMP.days}
    onkeydown={(e) => {
      if (e.key === 'Enter' && IMP.status !== 'loading') scan()
    }}
  />
  <span class="muted">days</span>
  <button class="btn primary" class:busy={IMP.status === 'loading'} disabled={IMP.status === 'loading'} onclick={scan}>
    <Icon name="search" size={14} />Scan sent mail
  </button>
</div>

{#if IMP.status === 'idle'}
  <EmptyState title="Nothing scanned yet" hint="Pick a window and press Scan. Leave days empty to scan everything." />
{:else if IMP.status === 'loading'}
  <Loading text="Scanning your Sent folder over IMAP… this can take a while." />
{:else if IMP.status === 'error'}
  <ErrorBox message={IMP.error} onretry={scan} />
{:else if !IMP.rows.length}
  {#if IMP.exhausted}
    <EmptyState title="All caught up" hint="Every candidate was added or ignored. Scan again any time." />
  {:else}
    <EmptyState title="No new contacts found" hint="Everyone you've emailed in this window is already a contact or ignored." />
  {/if}
{:else}
  <div class="import-tools">
    <input class="ctl" type="search" placeholder="Filter candidates…" style="width:240px" bind:value={IMP.filter} />
    <button class="btn sm ghost" onclick={() => setAll(true)}>Select all</button>
    <button class="btn sm ghost" onclick={() => setAll(false)}>None</button>
    <span class="count">{countText}</span>
  </div>
  <div class="table-wrap">
    <table class="edit">
      <thead>
        <tr>
          <th class="narrow">Add</th><th>Name</th><th>Email</th><th>Company</th><th>Role</th><th>Stage</th><th>Source</th>
        </tr>
      </thead>
      <tbody>
        {#each IMP.rows as r (r)}
          <tr hidden={!vis.includes(r)}>
            <td class="narrow"><input type="checkbox" bind:checked={r.add} /></td>
            <td><input class="ctl" bind:value={r.name} /></td>
            <td class="muted" title={r.email}>{r.email}</td>
            <td><input class="ctl" bind:value={r.company} /></td>
            <td><input class="ctl" placeholder="Role" bind:value={r.role} /></td>
            <td><Select options={stages} bind:value={r.stage} /></td>
            <td><Select options={sources} bind:value={r.source} /></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <div class="import-foot">
    <span class="muted">Add creates contacts with the values above. Ignore hides those addresses from future scans for good.</span>
    <div class="btns">
      <Btn class="btn" disabled={!picked} onclick={() => commit(true)}>Ignore checked</Btn>
      <Btn class="btn primary" disabled={!picked} onclick={() => commit(false)}><Icon name="plus" size={14} />Add checked</Btn>
    </div>
  </div>
{/if}
