<script>
  // A button that disables itself and shows a spinner while its (async) onclick runs.
  let { onclick, class: cls = 'btn', disabled = false, children, ...rest } = $props()
  let busy = $state(false)

  async function run(e) {
    if (busy || !onclick) return
    busy = true
    try {
      await onclick(e)
    } finally {
      busy = false
    }
  }
</script>

<button {...rest} class="{cls} {busy ? 'busy' : ''}" disabled={disabled || busy} onclick={run}>
  {@render children?.()}
</button>
