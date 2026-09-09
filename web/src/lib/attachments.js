// Shared helpers for IMAP attachments (ThreadBlock chips + AttachmentPreview).

// Mirror of the server allowlist: these are served `Content-Disposition: inline`,
// so the browser can render them (in our preview modal or a plain tab).
export const isPreviewable = (t) => /^(application\/pdf|image\/(png|jpe?g|gif|webp|bmp)|text\/plain)$/i.test(t || '')

export const isImage = (t) => /^image\//i.test(t || '')

export function humanSize(n) {
  n = Number(n) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
