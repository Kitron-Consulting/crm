// One modal at a time: either the "New contact" form or a confirm dialog.
// Rendered by components/Modal.svelte.

export const modal = $state({ kind: null, props: null, onClose: null })

export function openModal(kind, props = {}, onClose = null) {
  modal.kind = kind
  modal.props = props
  modal.onClose = onClose
}

export function closeModal() {
  if (!modal.kind) return
  const cb = modal.onClose
  modal.kind = null
  modal.props = null
  modal.onClose = null
  if (cb) cb()
}

/** Resolves true/false. Esc, scrim click and Cancel all resolve false. */
export function confirmDialog({ title, body, ok = 'OK', danger = false }) {
  return new Promise((resolve) => {
    let done = false
    const finish = (v) => {
      if (done) return
      done = true
      closeModal()
      resolve(v)
    }
    openModal(
      'confirm',
      { title, body, ok, danger, onOk: () => finish(true), onCancel: () => finish(false) },
      () => finish(false),
    )
  })
}

export function newContactDialog() {
  if (modal.kind) return
  openModal('new')
}

/** Calendar "Set next action": optional prefilled date ("YYYY-MM-DD") and contact id. */
export function setNextDialog({ date = '', contactId = null } = {}) {
  openModal('setnext', { date, contactId })
}
