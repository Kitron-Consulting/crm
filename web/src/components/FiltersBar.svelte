<script>
  // Shared by Board, Contacts, Calendar and Timeline: stage pills, source, Overdue only,
  // No next action (hidden on the Calendar, which has an Unscheduled panel), Clear, "N of M".
  // `children` renders extra view-specific toggles before Clear (Timeline's "Hide closed").
  import Icon from './Icon.svelte'
  import { S, UI, stageStyle, toggleStageFilter, filtersActive, resetFilters } from '../lib/store.svelte.js'
  let { shown, noNext = true, label = '', children } = $props()
</script>

<div class="filters">
  <div class="seg">
    {#each S.stages as st (st)}
      {@const t = stageStyle(st)}
      <button
        type="button"
        class="seg-btn tint"
        class:on={UI.filters.stages.includes(st)}
        style:--h={t.h}
        style:--s={t.sat}
        onclick={() => toggleStageFilter(st)}>{st}</button
      >
    {/each}
  </div>
  <span class="sep"></span>
  <select class="ctl" bind:value={UI.filters.source}>
    <option value="">All sources</option>
    {#each S.sources as s (s)}<option value={s}>{s}</option>{/each}
  </select>
  <button type="button" class="btn sm" class:on={UI.filters.overdue} onclick={() => (UI.filters.overdue = !UI.filters.overdue)}>
    <Icon name="alert" size={13} />Overdue only
  </button>
  {#if noNext}
    <button type="button" class="btn sm" class:on={UI.filters.noNext} onclick={() => (UI.filters.noNext = !UI.filters.noNext)}>
      No next action
    </button>
  {/if}
  {@render children?.()}
  {#if filtersActive() || UI.q}
    <button type="button" class="btn sm ghost" onclick={resetFilters}><Icon name="close" size={13} />Clear</button>
  {/if}
  <span class="count">{label || (shown === S.contacts.length ? shown + ' contacts' : shown + ' of ' + S.contacts.length)}</span>
</div>
