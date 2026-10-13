// hermes-vitals: desktop half. Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build).
//
// Surfaces: route /vitals (sidebar row "Vitals" + ⌘K "Open Vitals") and a status-bar chip.
// Data: host.requestProfile(route, "cli.exec", { argv: ["--profile", p, "vitals", "--json"] })
//       -> a fresh, read-only `hermes vitals` process; refreshed every 15 s while the page is visible.
// Look: theme variables only (--ui-*), so it follows Nous dark and every other skin.

import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-vitals'
const ROUTE = '/vitals'
const REFRESH_MS = 15000
const CLI_TIMEOUT_MS = 30000

// ── data ────────────────────────────────────────────────────────────────────
async function resolveRoute() {
  const owner = host.state?.focusedSessionOwner?.get?.() || null
  const routes = await host.profileRoutes()
  const exact = owner ? routes.filter((r) => r.connectionId === owner.connectionId && r.profile === owner.profile) : []
  if (exact.length === 1) return exact[0]
  const fallback = routes.find((r) => r.primary) || routes.find((r) => r.mode === 'local') || routes[0]
  if (!fallback) throw new Error('No Hermes profile route available')
  return fallback
}

function parseCliJson(output) {
  const lines = String(output || '').split('\n').map((l) => l.trim()).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!lines[i].startsWith('{')) continue
    try { return JSON.parse(lines[i]) } catch { /* keep looking */ }
  }
  throw new Error(lines.slice(-2).join(' ') || 'empty output from hermes vitals')
}

async function fetchVitals() {
  const route = await resolveRoute()
  const res = await host.requestProfile(route, 'cli.exec',
    { argv: ['--profile', route.targetProfile, 'vitals', '--json'], timeout: CLI_TIMEOUT_MS / 1000 }, CLI_TIMEOUT_MS + 5000)
  if (res && res.blocked) throw new Error(res.hint || 'cli.exec blocked')
  const payload = parseCliJson(res && res.output)
  if (payload.error) throw new Error(payload.error)
  return payload
}

// One shared poller so the page and the status chip never double-fetch.
const store = { data: null, error: null, at: 0, listeners: new Set(), timer: null, inflight: null }
function notify() { store.listeners.forEach((fn) => fn()) }
async function refresh() {
  if (store.inflight) return store.inflight
  store.inflight = fetchVitals()
    .then((d) => { store.data = d; store.error = null; store.at = Date.now() })
    .catch((e) => { store.error = String(e && e.message || e) })
    .finally(() => { store.inflight = null; notify() })
  return store.inflight
}
function useVitals() {
  const [, bump] = useState(0)
  useEffect(() => {
    const fn = () => bump((n) => n + 1)
    store.listeners.add(fn)
    if (!store.data && !store.inflight) refresh()
    return () => store.listeners.delete(fn)
  }, [])
  return store
}
function startPolling(ctx) {
  const tick = () => { if (!document.hidden) refresh() }
  ctx.setInterval(tick, REFRESH_MS)
  ctx.addEventListener(document, 'visibilitychange', tick)
  refresh()
}

// ── theme ───────────────────────────────────────────────────────────────────
const V = (name, fallback) => `var(${name}, ${fallback})`
const C = {
  accent: V('--ui-accent', '#4a84fe'), red: V('--ui-red', '#ff7b72'), yellow: V('--ui-yellow', '#d29922'),
  green: V('--ui-green', '#3fb950'), cyan: V('--ui-cyan', '#39c5cf'), purple: V('--ui-purple', '#bc8cff'),
  orange: V('--ui-orange', '#ffa657'), t1: V('--ui-text-primary', '#e6edf3'), t2: V('--ui-text-secondary', '#b1bac4'),
  t3: V('--ui-text-tertiary', '#7d8590'), t4: V('--ui-text-quaternary', '#6e7681'),
  stroke: V('--ui-stroke-tertiary', '#30363d'), stroke2: V('--ui-stroke-secondary', '#30363d'),
  panel: V('--ui-bg-elevated', '#010409'), sunk: V('--ui-bg-tertiary', '#0d1117'),
}
function cssColor(varName, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
  return v || fallback
}
const MONO = 'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace'
const STATUS = {
  alert: { label: 'ALERT', fg: '#fff', bg: C.red, edge: C.red },
  watch: { label: 'WATCH', fg: C.yellow, bg: 'color-mix(in srgb, var(--ui-yellow, #d29922) 14%, transparent)', edge: 'color-mix(in srgb, var(--ui-yellow, #d29922) 35%, transparent)' },
  stable: { label: 'STABLE', fg: C.green, bg: 'color-mix(in srgb, var(--ui-green, #3fb950) 14%, transparent)', edge: C.stroke },
  idle: { label: 'IDLE', fg: C.t3, bg: 'transparent', edge: C.stroke },
}

// ── heartbeat trace ─────────────────────────────────────────────────────────
function Trace({ hourly, alert }) {
  const ref = useRef(null)
  useEffect(() => {
    const cv = ref.current
    if (!cv) return undefined
    const ctx = cv.getContext('2d')
    let raf = 0, start = performance.now(), last = 0
    const col = alert ? cssColor('--ui-red', '#ff7b72') : cssColor('--ui-accent', '#4a84fe')
    const bg = cssColor('--ui-bg-elevated', '#010409')
    function path(W, H) {
      const base = H * 0.62, step = W / hourly.length, pts = [[0, base]]
      hourly.forEach((v, i) => {
        const x = i * step
        if (!v) { pts.push([x + step, base]); return }
        const a = Math.min(1, 0.25 + Math.log10(1 + v) / 2.1) * H * 0.55
        pts.push([x + step * 0.15, base], [x + step * 0.28, base - a * 0.15], [x + step * 0.36, base], [x + step * 0.44, base + a * 0.18],
          [x + step * 0.52, base - a], [x + step * 0.6, base + a * 0.32], [x + step * 0.7, base], [x + step * 0.82, base - a * 0.22], [x + step, base])
      })
      return { pts, base }
    }
    let geo = null
    function size() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const r = cv.getBoundingClientRect()
      cv.width = Math.max(1, Math.round(r.width * dpr)); cv.height = Math.max(1, Math.round(r.height * dpr))
      geo = path(cv.width, cv.height)
    }
    function draw(now) {
      raf = requestAnimationFrame(draw)
      if (now - last < 33) return
      last = now
      const W = cv.width, H = cv.height
      ctx.clearRect(0, 0, W, H)
      const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
      const sweep = reduce ? W : ((now - start) / 6000 % 1) * W
      ctx.lineJoin = 'round'; ctx.lineWidth = Math.max(1.6, H / 90); ctx.strokeStyle = col
      ctx.shadowColor = col; ctx.shadowBlur = 8
      ctx.beginPath()
      geo.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
      ctx.stroke()
      ctx.shadowBlur = 0
      if (!reduce) { // the erase bar that sweeps ahead, like a bedside monitor
        ctx.fillStyle = bg; ctx.fillRect(sweep, 0, W * 0.04, H)
        const y = geo.base
        ctx.fillStyle = col; ctx.beginPath(); ctx.arc(sweep, y, Math.max(2.5, H / 40), 0, 7); ctx.fill()
      }
    }
    size()
    const ro = new ResizeObserver(size); ro.observe(cv)
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [hourly, alert])
  return jsx('canvas', {
    ref,
    style: {
      display: 'block', width: '100%', height: 132, borderRadius: 6,
      background: `linear-gradient(color-mix(in srgb, var(--ui-accent, #4a84fe) 7%, transparent) 1px, transparent 1px) 0 0/100% 22px, linear-gradient(90deg, color-mix(in srgb, var(--ui-accent, #4a84fe) 7%, transparent) 1px, transparent 1px) 0 0/22px 100%`,
    },
  })
}

// ── pieces ──────────────────────────────────────────────────────────────────
const fmtAgo = (ts) => {
  if (!ts) return 'never'
  const h = (Date.now() / 1000 - ts) / 3600
  return h < 1 ? 'within the hour' : h < 48 ? `${Math.round(h)} h ago` : `${(h / 24).toFixed(1)} days ago`
}
const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '')

function Vital({ k, value, unit, note, color, bad }) {
  return jsxs('div', {
    children: [
      jsx('div', { style: { fontSize: 10, letterSpacing: '.08em', color, opacity: 0.9 }, children: k }),
      jsxs('div', {
        style: { fontSize: 30, fontWeight: 700, lineHeight: 1, color: bad ? C.red : color, fontVariantNumeric: 'tabular-nums' },
        children: [value, unit ? jsx('span', { style: { fontSize: 11, fontWeight: 500, marginLeft: 3, opacity: 0.8 }, children: unit }) : null],
      }),
      jsx('div', { style: { fontSize: 9.5, color: C.t4, marginTop: 2 }, children: note }),
    ],
  })
}

function Monitor({ bot, th }) {
  const st = STATUS[bot.status] || STATUS.idle
  const v = bot.vitals, bad = (k) => bot.bad.includes(k)
  return jsxs('div', {
    style: { background: C.panel, border: `1px solid ${st.edge}`, borderRadius: 10, padding: '10px 12px', fontFamily: MONO,
      boxShadow: bot.status === 'alert' ? '0 0 0 1px color-mix(in srgb, var(--ui-red, #ff7b72) 25%, transparent)' : 'none' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 10 },
        children: [
          jsx('span', { style: { fontSize: 15, fontWeight: 700, color: C.t1, letterSpacing: '.03em' }, children: bot.name.toUpperCase() }),
          jsx('span', { style: { fontSize: 10.5, color: C.t4 }, children: bot.profile }),
          jsx('span', { style: { marginLeft: 'auto', fontSize: 10.5, fontWeight: 700, letterSpacing: '.08em', padding: '2px 8px', borderRadius: 4, color: st.fg, background: st.bg }, children: st.label }),
        ],
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 12, marginTop: 8 },
        children: [
          jsxs('div', {
            style: { flex: 1, minWidth: 0 },
            children: [
              jsx(Trace, { hourly: bot.hourly, alert: bot.status === 'alert' }),
              jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.t4, marginTop: 3 },
                children: [jsx('span', { children: `${bot.hourly.length / 24}d ago` }), jsx('span', { children: bot.no_pulse ? `no pulse · last ${fmtAgo(bot.last_activity)}` : `last activity ${fmtAgo(bot.last_activity)}` })] }),
            ],
          }),
          jsxs('div', {
            style: { width: 220, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 10px' },
            children: [
              jsx(Vital, { k: 'HR', value: v.hr, unit: '/h', note: 'calls per active hour', color: C.green }),
              jsx(Vital, { k: 'BP', value: v.bp[0], unit: `/${v.bp[1]}`, note: `fail ${v.fail_pct}% · <${th.fail_pct}%`, color: C.yellow, bad: bad('bp') }),
              jsx(Vital, { k: 'TEMP', value: v.temp, note: `errors · 0–${th.errors}`, color: C.orange, bad: bad('temp') }),
              jsx(Vital, { k: 'SAT', value: v.sat == null ? '–' : Math.round(v.sat), unit: v.sat == null ? '' : '%', note: `memory · alarm >${th.mem_pct}%`, color: C.cyan, bad: bad('sat') }),
              jsx(Vital, { k: 'RR', value: v.rr, note: `loops · 0–${th.loops}`, color: C.purple, bad: bad('rr') }),
              jsx(Vital, { k: 'NEXT', value: bot.next_memory_review ?? '–', unit: 'msg', note: 'to memory review', color: C.t2 }),
            ],
          }),
        ],
      }),
    ],
  })
}

function Alarm({ bot, a }) {
  const red = a.level === 'alert'
  const ev = a.evidence || {}
  const evText = [ev.file, ev.source, ev.count != null ? `×${ev.count}` : null, ev.last ? `last ${fmtTime(ev.last)}` : null].filter(Boolean).join(' · ')
  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: '16px 96px 1.3fr 1fr 1fr', gap: 12, padding: '8px 0', borderTop: `1px solid ${C.stroke}`, fontSize: 12, alignItems: 'start' },
    children: [
      jsx('span', { style: { width: 9, height: 9, borderRadius: '50%', marginTop: 4, background: red ? C.red : C.yellow, boxShadow: `0 0 8px ${red ? C.red : C.yellow}` } }),
      jsx('span', { style: { fontFamily: MONO, fontWeight: 700, color: red ? C.red : C.yellow }, children: bot.name.toUpperCase() }),
      jsxs('span', { children: [jsx('div', { style: { color: C.t1, fontWeight: 600 }, children: a.finding }), jsx('div', { style: { color: C.t3, fontSize: 11.5 }, children: `${a.reading} · normal ${a.normal}` })] }),
      jsx('span', { style: { fontFamily: MONO, fontSize: 10.5, color: C.t4 }, children: evText }),
      jsxs('span', { style: { color: C.t2, fontSize: 11.5 }, children: [a.treatment,
        // The symptom is here; the cause (which login, benched until when, the fix) is in Key Doctor.
        a.rule === 'provider_auth' ? jsx('button', { type: 'button', onClick: () => host.navigate('/keys'),
          style: { display: 'block', marginTop: 6, fontSize: 11, padding: '3px 9px', borderRadius: 6, border: `1px solid ${C.stroke}`, background: 'transparent', color: C.accent, cursor: 'pointer' },
          children: 'Open Key Doctor →' }) : null] }),
    ],
  })
}

function VitalsPage() {
  const s = useVitals()
  const d = s.data
  const head = jsxs('div', {
    style: { display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 12 },
    children: [
      jsx('div', { style: { fontSize: 13, color: C.t1, fontWeight: 600 }, children: 'Vitals' }),
      jsx('div', { style: { fontSize: 11.5, color: C.t3 }, children: 'read-only · counts only · no message text' }),
      jsx('div', {
        style: { marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: C.t3 },
        children: d ? `WARD · ${d.ward.bots} BOTS · LAST ${d.window_days} DAYS · ${d.ward.alert} ALERT · ${d.ward.watch} WATCH · updated ${new Date(s.at).toLocaleTimeString()}` : s.error ? '' : 'reading your bots…',
      }),
    ],
  })
  if (!d) {
    return jsxs('div', { style: { padding: '20px 28px', height: '100%', overflow: 'auto' }, children: [head,
      s.error ? jsx('div', { style: { color: C.red, fontSize: 12.5 }, children: `Could not read vitals: ${s.error}. Is the hermes-vitals Python plugin enabled (plugins.enabled in config.yaml)?` }) : null] })
  }
  const alarms = d.bots.flatMap((b) => b.alarms.map((a) => ({ b, a })))
  alarms.sort((x, y) => (x.a.level === 'alert' ? 0 : 1) - (y.a.level === 'alert' ? 0 : 1))
  const th = d.thresholds
  return jsxs('div', {
    style: { padding: '20px 28px 28px', height: '100%', overflow: 'auto' },
    children: [
      head,
      jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(560px, 1fr))', gap: 14 },
        children: d.bots.map((b) => jsx(Monitor, { bot: b, th }, b.profile)) }),
      jsxs('div', {
        style: { marginTop: 14, background: C.panel, border: `1px solid ${C.stroke}`, borderRadius: 10, padding: '10px 14px' },
        children: [
          jsx('div', { style: { fontSize: 10.5, letterSpacing: '.09em', color: C.t3, textTransform: 'uppercase', marginBottom: 4 }, children: `Alarms · ${alarms.length} · finding · reading vs normal · evidence · treatment` }),
          alarms.length ? alarms.map(({ b, a }, i) => jsx(Alarm, { bot: b, a }, `${b.profile}-${i}`)) : jsx('div', { style: { color: C.green, fontSize: 12, padding: '8px 0' }, children: 'No alarms.' }),
          jsx('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 8, fontFamily: MONO, fontSize: 10.5, color: C.t4 },
            children: [['HR', C.green, 'tool calls per active hour'], ['BP', C.yellow, 'failed calls / stuck sessions'], ['TEMP', C.orange, 'errors in errors.log'],
              ['SAT', C.cyan, 'memory fill vs cap'], ['RR', C.purple, 'loops (same failing step)'], ['NEXT', C.t2, 'messages to the next memory review']]
              .map(([k, c, t]) => jsxs('span', { children: [jsx('b', { style: { color: c }, children: k }), ` ${t}`] }, k)) }),
          jsx('div', { style: { marginTop: 8, fontSize: 11, color: C.t4 },
            children: 'Counts only what the logs record: zero failures is not the same as "it worked". Normal ranges are Hermes Vitals thresholds, not Hermes defaults.' }),
        ],
      }),
    ],
  })
}

function StatusChip() {
  const s = useVitals()
  const d = s.data
  if (!d) return null
  const n = d.ward.alert, w = d.ward.watch
  const color = n ? C.red : w ? C.yellow : C.green
  return jsx('button', {
    type: 'button', title: 'Open Vitals', onClick: () => host.navigate(ROUTE),
    style: { background: 'transparent', border: 0, cursor: 'pointer', padding: '0 6px', fontSize: 11, color, fontFamily: MONO },
    children: n ? `♥ ${n} alert` : w ? `♥ ${w} watch` : '♥ stable',
  })
}

export default {
  id: ID,
  name: 'Vitals',
  register(ctx) {
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(VitalsPage, {}) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, order: 86, data: { path: ROUTE, label: 'Vitals', codicon: 'pulse' } })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: `${ID}.open`, label: 'Open Vitals', keywords: ['vitals', 'health', 'monitor', 'errors', 'memory'], run: () => host.navigate(ROUTE) } })
    ctx.register({ id: 'statusbar', area: STATUSBAR_AREAS.right, order: 131, render: () => jsx(StatusChip, {}) })
    startPolling(ctx)
  },
}
