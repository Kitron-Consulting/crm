<script>
  let { title, sub = '', children } = $props()
</script>

<div class="view-head">
  <div>
    <h1>{title}</h1>
    {#if sub}<div class="sub">{sub}</div>{/if}
  </div>
  <div class="spacer"></div>
  {@render children?.()}
</div>
