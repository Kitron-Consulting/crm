<script>
  import { onMount } from 'svelte'
  import { modal, closeModal } from '../lib/modal.svelte.js'
  import ConfirmDialog from './ConfirmDialog.svelte'
  import NewContactModal from './NewContactModal.svelte'
  import SetNextModal from './SetNextModal.svelte'

  // Rendered only while a modal is open (App.svelte guards with {#if}),
  // so mounting == opening: fade in on the next frame.
  let show = $state(false)
  onMount(() => requestAnimationFrame(() => (show = true)))
</script>

<div
  class="modal-wrap"
  class:show
  role="presentation"
  onmousedown={(e) => {
    if (e.target === e.currentTarget) closeModal()
  }}
>
  <div class="modal" role="dialog" aria-modal="true">
    {#if modal.kind === 'confirm'}
      <ConfirmDialog {...modal.props} />
    {:else if modal.kind === 'new'}
      <NewContactModal />
    {:else if modal.kind === 'setnext'}
      <SetNextModal {...modal.props} />
    {/if}
  </div>
</div>
