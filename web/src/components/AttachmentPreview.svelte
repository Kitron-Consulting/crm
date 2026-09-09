<script>
  // Lightbox for previewable IMAP attachments (see lib/preview.svelte.js).
  // Rendered only while preview.item is set (App.svelte guards with {#if}),
  // so mounting == opening. Images go in an <img>; PDF and text/plain go in an
  // <iframe> the browser renders with its built-in viewer (the server sends
  // them inline). No `sandbox` on the iframe: Chrome refuses to show its PDF
  // viewer inside a sandboxed frame, and this is same-origin content from our
  // own allowlisted endpoint (never HTML/SVG).
  import { onMount } from 'svelte'
  import Icon from './Icon.svelte'
  import Loading from './Loading.svelte'
  import ErrorBox from './ErrorBox.svelte'
  import { preview, closePreview } from '../lib/preview.svelte.js'
  import { attachmentUrl } from '../lib/api.js'
  import { humanSize, isImage } from '../lib/attachments.js'

  const m = $derived(preview.item.m)
  const a = $derived(preview.item.a)
  const name = $derived(a.name || 'attachment')
  const id = $derived(`${m.folder}|${m.uid}|${a.part}`)
  const src = $derived(attachmentUrl(m, a))
  const dl = $derived(attachmentUrl(m, a, { download: true }))
  const image = $derived(isImage(a.type))

  // 'loading' | 'ok' | 'error'; reset when another attachment replaces this one.
  let status = $state('loading')
  $effect(() => { id; status = 'loading' })

  let show = $state(false)
  let closeBtn = $state()
  onMount(() => requestAnimationFrame(() => { show = true; closeBtn?.focus() }))
</script>

<div
  class="modal-wrap preview-wrap"
  class:show
  role="presentation"
  onmousedown={(e) => { if (e.target === e.currentTarget) closePreview() }}
>
  <div class="preview-panel" role="dialog" aria-modal="true" aria-label={name} tabindex="-1">
    <header class="pv-head">
      <div class="pv-title">
        <span class="pv-name" title={name}>{name}</span>
        <span class="pv-meta">{humanSize(a.size)}{a.type ? ` · ${a.type}` : ''}</span>
      </div>
      <a class="btn sm" href={src} target="_blank" rel="noopener noreferrer"><Icon name="open" size={13} />Open in new tab</a>
      <a class="btn sm" href={dl} download={name}><Icon name="download" size={13} />Download</a>
      <button class="btn ghost sm icon" title="Close (Esc)" aria-label="Close preview" bind:this={closeBtn} onclick={closePreview}>
        <Icon name="close" size={15} />
      </button>
    </header>
    <div class="pv-body" class:fill={!image}>
      {#key id}
        {#if status !== 'error'}
          {#if image}
            <img class="pv-media" class:pending={status !== 'ok'} {src} alt={name}
                 onload={() => (status = 'ok')} onerror={() => (status = 'error')} />
          {:else}
            <iframe class="pv-media" class:pending={status !== 'ok'} {src} title={name}
                    onload={() => (status = 'ok')} onerror={() => (status = 'error')}></iframe>
          {/if}
        {/if}
      {/key}
      {#if status === 'loading'}
        <div class="pv-overlay"><Loading text="Loading preview…" /></div>
      {:else if status === 'error'}
        <div class="pv-overlay"><ErrorBox message="Couldn't load preview — use Open in new tab or Download above." /></div>
      {/if}
    </div>
  </div>
</div>
