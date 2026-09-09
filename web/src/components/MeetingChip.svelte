<script>
  // Next upcoming meeting for a contact (from the synced mail cache).
  import Icon from './Icon.svelte'
  import { meetingWhen } from '../lib/store.svelte.js'
  let { contact: c } = $props()
  const m = $derived(c.next_meeting)
  const w = $derived(meetingWhen(m))
</script>

{#if m && w}
  <span
    class="chip meeting"
    class:soon={w.soon}
    title={(m.summary || 'Meeting') + ' · ' + m.start + (m.location ? ' @ ' + m.location : '')}
  >
    <Icon name="calendar" size={12} />
    <span class="chip-date">{w.day}{w.time ? ' · ' + w.time : ''}</span>
    {#if w.rel}<span class="chip-rel">{w.rel}</span>{/if}
  </span>
{/if}
