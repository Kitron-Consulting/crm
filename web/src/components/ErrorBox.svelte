<script>
  import Icon from './Icon.svelte'
  let { message, onretry } = $props()
</script>

<div class="error-box">
  <Icon name="alert" size={16} />
  <span class="spacer">{message}</span>
  {#if onretry}<button class="btn sm" onclick={onretry}>Retry</button>{/if}
</div>
