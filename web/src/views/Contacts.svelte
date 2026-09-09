<script>
  import ViewHead from '../components/ViewHead.svelte'
  import FiltersBar from '../components/FiltersBar.svelte'
  import StageChip from '../components/StageChip.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import ErrorBox from '../components/ErrorBox.svelte'
  import SkeletonRows from '../components/SkeletonRows.svelte'
  import { S, UI, visibleContacts, openDrawer, loadState, toggleSort, cmpStr, dueStatus, dateWithRel } from '../lib/store.svelte.js'

  const COLS = [
    { key: 'name', label: 'Name', sort: true },
    { key: 'company', label: 'Company', sort: true },
    { key: 'role', label: 'Role' },
    { key: 'email', label: 'Email' },
    { key: 'stage', label: 'Stage', sort: true },
    { key: 'source', label: 'Source', sort: true },
    { key: 'next_action', label: 'Next action' },
    { key: 'next_date', label: 'Due', sort: true },
  ]

  function sortContacts(list) {
    const { key, dir } = UI.sort
    return list.slice().sort((a, b) => {
      if (key === 'stage') return (S.stages.indexOf(a.stage) - S.stages.indexOf(b.stage)) * dir || cmpStr(a.name, b.name)
      const x = (a[key] || '').toLowerCase()
      const y = (b[key] || '').toLowerCase()
      if (key === 'next_date') {
        // Empty due dates always sink to the bottom.
        if (!x && y) return 1
        if (x && !y) return -1
      }
      return (x < y ? -1 : x > y ? 1 : 0) * dir || cmpStr(a.name, b.name)
    })
  }
  const list = $derived(sortContacts(visibleContacts()))
</script>

{#if S.error}
  <ErrorBox message={"Couldn't load data: " + S.error} onretry={loadState} />
{:else if !S.loaded}
  <SkeletonRows />
{:else}
  <ViewHead title="Contacts" sub="Click a column to sort, a row to open." />
  <FiltersBar shown={list.length} />
  {#if !S.contacts.length}
    <EmptyState title="No contacts yet" hint="Press n to add one." />
  {:else if !list.length}
    <EmptyState title="No matches" hint="Try a different search or clear the filters." />
  {:else}
    <div class="table-wrap">
      <table class="rows">
        <thead>
          <tr>
            {#each COLS as col (col.key)}
              <th class:sortable={col.sort} onclick={col.sort ? () => toggleSort(col.key) : undefined}>
                {col.label}
                {#if col.sort && UI.sort.key === col.key}<span class="arrow">{UI.sort.dir > 0 ? '▲' : '▼'}</span>{/if}
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each list as c (c.id)}
            <tr onclick={() => openDrawer(c.id)}>
              <td class="strong" title={c.name}>{c.name || '(no name)'}</td>
              <td title={c.company}>{c.company || ''}</td>
              <td class="muted" title={c.role}>{c.role || ''}</td>
              <td class="muted" title={c.email}>{c.email || ''}</td>
              <td><StageChip stage={c.stage} small /></td>
              <td class="muted">{c.source || ''}</td>
              <td title={c.next_action}>{c.next_action || ''}</td>
              <td>{#if c.next_date}<span class="date {dueStatus(c)}">{dateWithRel(c.next_date)}</span>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}
