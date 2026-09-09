<script>
  import Icon from './Icon.svelte'
  import { toasts } from '../lib/toast.svelte.js'
  const iconFor = (k) => (k === 'ok' ? 'check' : k === 'error' || k === 'warn' ? 'alert' : 'info')
</script>

<div class="toasts">
  {#each toasts as t (t.id)}
    <div class="toast {t.kind}" class:show={t.show} role="status">
      <Icon name={iconFor(t.kind)} size={14} />
      <span>{t.msg}</span>
    </div>
  {/each}
</div>
