// Toast queue. Rendered by components/Toasts.svelte.

export const toasts = $state([])
let seq = 0

function find(id) {
  return toasts.find((t) => t.id === id)
}

export function toast(msg, kind = 'info') {
  const id = ++seq
  toasts.push({ id, msg, kind, show: false })
  // Add .show on the next frame so the enter transition plays.
  requestAnimationFrame(() => {
    const t = find(id)
    if (t) t.show = true
  })
  setTimeout(
    () => {
      const t = find(id)
      if (t) t.show = false
      setTimeout(() => {
        const i = toasts.findIndex((t) => t.id === id)
        if (i >= 0) toasts.splice(i, 1)
      }, 200)
    },
    kind === 'error' ? 5200 : 2800,
  )
}
