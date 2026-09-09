<script>
  // <select> over a string list. If the current value isn't in the list
  // (e.g. a stage that was removed from config) it's shown as the first option.
  let { value = $bindable(), options = [], onchange, class: cls = 'ctl', ...rest } = $props()
  const list = $derived(value && !options.includes(value) ? [value, ...options] : options)
</script>

<select {...rest} class={cls} bind:value onchange={() => onchange?.(value)}>
  {#each list as o (o)}
    <option value={o}>{o}</option>
  {/each}
</select>
