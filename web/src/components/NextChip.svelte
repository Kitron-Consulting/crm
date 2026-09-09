<script>
  import Icon from './Icon.svelte'
  import { dueStatus, dateWithRel, relDue, hasNext } from '../lib/store.svelte.js'
  let { contact: c } = $props()
  const st = $derived(dueStatus(c))
  const urgent = $derived(st === 'overdue' || st === 'soon')
  const dateLabel = $derived(urgent && relDue(c.next_date) ? relDue(c.next_date) : c.next_date)
</script>

{#if hasNext(c)}
  <span
    class="chip next {st}"
    title={(c.next_action || '') + (c.next_date ? ' — ' + dateWithRel(c.next_date) : '')}
  >
    <Icon name={st === 'overdue' ? 'alert' : 'clock'} size={12} />
    {#if c.next_action}<span class="chip-text">{c.next_action}</span>{/if}
    {#if c.next_date}<span class="chip-date">{dateLabel}</span>{/if}
  </span>
{/if}
