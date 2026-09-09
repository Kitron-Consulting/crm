// In-app preview (lightbox) for previewable IMAP attachments. One at a time;
// opening another replaces it. Rendered by components/AttachmentPreview.svelte,
// closed first in the Esc chain (preview → modal → drawer → search).

export const preview = $state({ item: null })

/** `item` = { m: message, a: attachment, returnTo: HTMLElement|null } */
export function openPreview(m, a, returnTo = null) {
  preview.item = { m, a, returnTo }
}

export function closePreview() {
  const it = preview.item
  if (!it) return
  preview.item = null
  // Return focus to the chip that opened it (if it is still in the DOM).
  const el = it.returnTo
  if (el && el.isConnected) requestAnimationFrame(() => el.focus())
}
