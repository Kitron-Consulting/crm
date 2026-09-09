<script>
  import { stageStyle } from '../lib/store.svelte.js'
  let { stage, small = false } = $props()
  const t = $derived(stageStyle(stage))
</script>

<span class="chip stage tint" class:sm={small} style:--h={t.h} style:--s={t.sat}>{stage || '—'}</span>
