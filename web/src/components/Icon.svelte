<script>
  import { ICONS } from '../lib/icons.js'
  let { name, size = 16 } = $props()
</script>

<svg
  class="ico"
  viewBox="0 0 24 24"
  width={size}
  height={size}
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  <path d={ICONS[name] || ICONS.info} />
</svg>
