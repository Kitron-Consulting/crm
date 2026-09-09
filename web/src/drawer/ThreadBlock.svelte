<script>
  // Lazy-loaded IMAP conversation, cached per email address for the session.
  import Icon from '../components/Icon.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import ErrorBox from '../components/ErrorBox.svelte'
  import Loading from '../components/Loading.svelte'
  import { threadCache, loadThread } from '../lib/store.svelte.js'
  import { attachmentUrl } from '../lib/api.js'
  import { isPreviewable, humanSize } from '../lib/attachments.js'
  import { openPreview } from '../lib/preview.svelte.js'

  let { contact: c } = $props()
  const key = $derived((c.email || '').toLowerCase())
  const entry = $derived(key ? threadCache[key] : undefined)

  $effect(() => {
    if (key && !threadCache[key]) loadThread(c.email)
  })
  const refresh = () => delete threadCache[key]

  // Quoted history ("On … wrote:" tails) is split off server-side; hide it
  // behind a per-message toggle. Reset when the drawer moves to another contact.
  let showQuoted = $state({})
  $effect(() => { key; showQuoted = {} })
  const quotedLines = (q) => q.split('\n').length

  // ---------- calendar events (text/calendar parts, parsed server-side) ----------
  const EVENT_STATUS = {
    invitation: { label: 'Invitation', cls: 'accent' },
    accepted:   { label: 'Accepted',   cls: 'ok' },
    declined:   { label: 'Declined',   cls: 'danger' },
    tentative:  { label: 'Tentative',  cls: 'warn' },
    cancelled:  { label: 'Cancelled',  cls: 'danger' },
    reply:      { label: 'Reply',      cls: 'muted' },
    event:      { label: 'Event',      cls: 'muted' },
    link:       { label: 'Meeting link', cls: 'muted' },
  }
  const eventStatus = (ev) => EVENT_STATUS[ev.status] || { label: ev.status || 'Event', cls: 'muted' }

  const JOIN_LABEL = {
    teams: 'Join Teams meeting', meet: 'Join Google Meet', zoom: 'Join Zoom', webex: 'Join Webex',
  }
  const PROVIDER_NAME = { teams: 'Microsoft Teams', meet: 'Google Meet', zoom: 'Zoom', webex: 'Webex' }
  const joinLabel = (ev) => JOIN_LABEL[ev.provider] || 'Join meeting'
  const providerName = (ev) => PROVIDER_NAME[ev.provider] || ''

  // "start – end"; all-day events show date(s) plus "All day"; missing end → start only.
  function eventTime(ev) {
    const s = ev.start || '', e = ev.end || ''
    if (!s && !e) return ''
    if (ev.all_day) {
      const range = e && e !== s ? `${s} – ${e}` : s
      return `${range} · All day`
    }
    if (!e) return s
    // Same-day range: "2026-09-15 10:00 – 11:00" instead of repeating the date.
    const sd = s.slice(0, 10), ed = e.slice(0, 10)
    if (sd === ed && s.length > 10 && e.length > 10) return `${s} – ${e.slice(11).trim()}`
    return `${s} – ${e}`
  }

  // ---------- attachments ----------
  // Previewable chips stay real <a href>s (middle/ctrl-click → new tab still works);
  // a plain left-click is intercepted to open the in-app preview instead.
  function onPreviewClick(e, m, a) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    openPreview(m, a, e.currentTarget)
  }
  function shortName(name, max = 28) {
    name = name || 'attachment'
    if (name.length <= max) return name
    // Keep the extension visible: "Tarjouspyyntö_QC_lin….pdf"
    const dot = name.lastIndexOf('.')
    const ext = dot > 0 && name.length - dot <= 6 ? name.slice(dot) : ''
    const head = name.slice(0, Math.max(1, max - ext.length - 1))
    return `${head}…${ext}`
  }
</script>

<section class="block thread">
  {#if !c.email}
    <EmptyState title="No email address" hint="Add an email in Details to load the conversation." />
  {:else if entry?.messages}
    {@const msgs = entry.messages}
    <div class="thread-tools">
      <span>{msgs.length} {msgs.length === 1 ? 'message' : 'messages'} with {c.email}</span>
      <button class="btn sm ghost" onclick={refresh}><Icon name="refresh" size={13} />Refresh</button>
    </div>
    {#if !msgs.length}
      <EmptyState title="No messages" hint="Nothing found over IMAP for this address." />
    {/if}
    {#each msgs as m, i}
      {@const atts = m.attachments || []}
      <article class="msg {m.direction || ''}">
        <header>
          <span class="dir {m.direction || ''}">{m.direction === 'inbound' ? 'in' : 'out'}</span>
          <span class="msg-subject" title={m.subject}>{m.subject || '(no subject)'}</span>
          {#if atts.length}
            <span class="msg-attach-count" title="{atts.length} {atts.length === 1 ? 'attachment' : 'attachments'}">
              <Icon name="paperclip" size={12} />{atts.length}
            </span>
          {/if}
          <time class="date">{m.date || ''}</time>
        </header>
        <div class="msg-meta" title="{m.from || ''} → {m.to || ''}">{m.from || ''} → {m.to || ''}</div>

        {#if m.event}
          {@const ev = m.event}
          {@const st = eventStatus(ev)}
          {@const when = eventTime(ev)}
          <div class="msg-event {ev.status || ''}">
            <div class="msg-event-head">
              <span class="msg-event-ico"><Icon name="calendar" size={15} /></span>
              <span class="msg-event-title" class:struck={ev.status === 'cancelled'} title={ev.summary || m.subject}>
                {ev.summary || m.subject || '(untitled event)'}
              </span>
              <span class="msg-event-badge {st.cls}">{st.label}</span>
            </div>
            {#if when}
              <div class="msg-event-row"><Icon name="clock" size={13} /><span>{when}</span></div>
            {/if}
            {#if ev.location}
              <div class="msg-event-row"><Icon name="pin" size={13} /><span title={ev.location}>{ev.location}</span></div>
            {/if}
            {#if ev.organizer}
              <div class="msg-event-row"><Icon name="user" size={13} /><span title={ev.organizer}>{ev.organizer}</span></div>
            {/if}
            {#if ev.join_url}
              <div class="msg-event-join">
                <a class="btn sm primary" href={ev.join_url} target="_blank" rel="noopener noreferrer">
                  <Icon name="open" size={13} />{joinLabel(ev)}
                </a>
                {#if providerName(ev)}<span class="msg-event-provider">{providerName(ev)}</span>{/if}
              </div>
            {/if}
          </div>
        {/if}

        <div class="msg-body">{m.body || ''}</div>
        {#if m.quoted}
          <button class="quote-toggle" onclick={() => { showQuoted[i] = !showQuoted[i] }}>
            {showQuoted[i] ? '▾ Hide quoted text' : `▸ Show quoted text (${quotedLines(m.quoted)} lines)`}
          </button>
          {#if showQuoted[i]}<div class="msg-quoted">{m.quoted}</div>{/if}
        {/if}

        {#if atts.length}
          <div class="msg-attachments">
            {#each atts as a}
              {@const preview = isPreviewable(a.type)}
              {@const label = `${a.name || 'attachment'} · ${humanSize(a.size)}${a.type ? ' · ' + a.type : ''}`}
              <span class="attach-chip" class:preview>
                {#if preview}
                  <a class="attach-main" href={attachmentUrl(m, a)} target="_blank" rel="noopener noreferrer"
                     title="{label} · click to preview" onclick={(e) => onPreviewClick(e, m, a)}>
                    <Icon name="paperclip" size={12} />
                    <span class="attach-name">{shortName(a.name)}</span>
                    <span class="attach-size">{humanSize(a.size)}</span>
                  </a>
                  <a class="attach-dl" href={attachmentUrl(m, a, { download: true })} download={a.name || ''}
                     title="Download" aria-label="Download {a.name || 'attachment'}">
                    <Icon name="download" size={12} />
                  </a>
                {:else}
                  <a class="attach-main" href={attachmentUrl(m, a)} download={a.name || ''} title={label}>
                    <Icon name="paperclip" size={12} />
                    <span class="attach-name">{shortName(a.name)}</span>
                    <span class="attach-size">{humanSize(a.size)}</span>
                  </a>
                {/if}
              </span>
            {/each}
          </div>
        {/if}
      </article>
    {/each}
  {:else if entry?.error}
    <ErrorBox message={entry.error} onretry={refresh} />
  {:else}
    <Loading text="Loading thread over IMAP… this can take a moment." />
  {/if}
</section>
