<script>
  let { title, hint = '', children } = $props()
</script>

<div class="empty">
  <strong>{title}</strong>
  {#if hint}<span>{hint}</span>{/if}
  {@render children?.()}
</div>
