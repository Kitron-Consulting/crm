<script>
  import Icon from '../components/Icon.svelte'
  import Btn from '../components/Btn.svelte'
  import NextForm from '../components/NextForm.svelte'
  import { UI, dueStatus, hasNext, dateWithRel, mutate } from '../lib/store.svelte.js'
  let { contact: c } = $props()
  const st = $derived(dueStatus(c))
</script>

<section class="block next-block {st}">
  <div class="block-title"><span>Next action</span></div>
  {#if UI.drawer.editingNext}
    <NextForm contact={c} oncancel={() => (UI.drawer.editingNext = false)} />
  {:else if !hasNext(c)}
    <div class="next-card">
      <span class="muted">No next action.</span>
      <div class="btns">
        <button class="btn sm" onclick={() => (UI.drawer.editingNext = true)}><Icon name="plus" size={13} />Set next</button>
      </div>
    </div>
  {:else}
    <div class="next-card">
      <div style="min-width:0">
        <div class="next-action">{c.next_action || '—'}</div>
        {#if c.next_date}<div class="date {st}">{dateWithRel(c.next_date)}</div>{/if}
      </div>
      <div class="btns">
        <Btn class="btn sm" onclick={() => mutate('/api/contacts/done', { id: c.id, name: c.name }, 'Marked done')}>
          <Icon name="check" size={14} />Done
        </Btn>
        <button class="btn sm ghost" onclick={() => (UI.drawer.editingNext = true)}><Icon name="edit" size={13} />Edit</button>
      </div>
    </div>
  {/if}
</section>
