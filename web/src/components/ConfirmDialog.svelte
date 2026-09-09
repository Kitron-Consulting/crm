<script>
  let { title, body, ok = 'OK', danger = false, onOk, onCancel } = $props()
  let okBtn = $state()
  $effect(() => okBtn?.focus())
</script>

<h2>{title}</h2>
<p class="lead">{body}</p>
<div class="modal-actions">
  <button class="btn" onclick={onCancel}>Cancel</button>
  <button bind:this={okBtn} class="btn {danger ? 'danger solid' : 'primary'}" onclick={onOk}>{ok}</button>
</div>
