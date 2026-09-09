<script>
  import { stageStyle } from '../lib/store.svelte.js'
  let { stage, small = false } = $props()
  const t = $derived(stageStyle(stage))
</script>

{#if stage}
  <span class="chip stage tint" class:sm={small} style:--h={t.h} style:--s={t.sat}>{stage}</span>
{:else}
  <span class="chip nostage" class:sm={small} title="No stage">—</span>
{/if}
