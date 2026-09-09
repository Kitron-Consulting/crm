<script>
  // Browse accounts (the org a contact belongs to). Click a row to open the
  // account panel (edit domain/notes, see linked contacts). Filtered by the
  // global search box (name or domain).
  import ViewHead from '../components/ViewHead.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import SkeletonRows from '../components/SkeletonRows.svelte'
  import { S, visibleAccounts, openAccount } from '../lib/store.svelte.js'

  const list = $derived(visibleAccounts())
</script>

{#if !S.loaded}
  <SkeletonRows />
{:else}
  <ViewHead title="Accounts" sub="Companies your contacts belong to — click to open." />
  <div class="filters">
    <span class="count">{list.length} {list.length === 1 ? 'account' : 'accounts'}</span>
  </div>
  {#if !S.accounts.length}
    <EmptyState title="No accounts yet" hint="Accounts appear automatically from your contacts' company names." />
  {:else if !list.length}
    <EmptyState title="No matches" hint="Try a different search." />
  {:else}
    <div class="table-wrap">
      <table class="rows">
        <thead>
          <tr><th>Account</th><th>Domain</th><th class="num">Contacts</th></tr>
        </thead>
        <tbody>
          {#each list as a (a.id)}
            <tr onclick={() => openAccount(a.id)}>
              <td class="strong" title={a.name}>{a.name}</td>
              <td class="muted" title={a.domain}>{a.domain || ''}</td>
              <td class="num">{a.contact_count}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}
