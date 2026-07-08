---
name: 燃人AI 1.0
description: Internal community-operations admin console — a dark navigation shell framing a light, evidence-dense workspace.
colors:
  teal-current: "#155e75"
  teal-current-hover: "#164e63"
  teal-current-active: "#083344"
  signal-cyan: "#22d3ee"
  link-cyan: "#0e7490"
  brand-base: "#0891b2"
  console-ink: "#020617"
  console-surface: "#0f172a"
  console-border: "#1e293b"
  content-bg: "#f1f5f9"
  panel-surface: "#ffffff"
  ink: "#020617"
  ink-body: "#334155"
  ink-muted: "#475569"
  hairline: "#e2e8f0"
  success: "#059669"
  online: "#10b981"
  danger: "#be123c"
  danger-hairline: "#fecdd3"
typography:
  display:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "1.875rem"
    fontWeight: 900
    lineHeight: 2rem
    letterSpacing: "-0.025em"
  title:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    lineHeight: 1.5rem
    letterSpacing: "normal"
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.5rem
    letterSpacing: "normal"
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1rem
    letterSpacing: "normal"
rounded:
  md: "6px"
  lg: "8px"
  xl: "12px"
  full: "9999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
components:
  button-primary:
    backgroundColor: "{colors.teal-current}"
    textColor: "{colors.panel-surface}"
    rounded: "{rounded.lg}"
    padding: "0 16px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.teal-current-hover}"
    textColor: "{colors.panel-surface}"
  button-primary-active:
    backgroundColor: "{colors.teal-current-active}"
    textColor: "{colors.panel-surface}"
  button-secondary:
    backgroundColor: "{colors.panel-surface}"
    textColor: "{colors.ink-body}"
    rounded: "{rounded.lg}"
    padding: "0 12px"
    height: "40px"
  button-danger:
    backgroundColor: "{colors.panel-surface}"
    textColor: "{colors.danger}"
    rounded: "{rounded.lg}"
    padding: "0 12px"
    height: "40px"
  input:
    backgroundColor: "{colors.panel-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "0 12px"
    height: "44px"
  panel:
    backgroundColor: "{colors.panel-surface}"
    textColor: "{colors.ink-body}"
    rounded: "{rounded.xl}"
    padding: "16px"
  nav-item-active:
    backgroundColor: "{colors.signal-cyan}"
    textColor: "{colors.console-ink}"
    rounded: "{rounded.lg}"
    padding: "10px 14px"
  status-pill:
    backgroundColor: "{colors.panel-surface}"
    textColor: "{colors.ink-body}"
    rounded: "{rounded.full}"
    padding: "4px 10px"
---

# Design System: 燃人AI 1.0

## 1. Overview

**Creative North Star: "The Operations Console"**

This is an internal operations console, not a product for sale. A **dark navigation shell** (near-black slate-950 sidebar and mobile header, lit by cyan accents) frames a **light, evidence-dense workspace** (cool slate-100 canvas, white panels). The dark chrome anchors the operator; the light workspace holds the work. That two-layer contrast is the system's spine — it tells staff at a glance where they *are* (navigation) versus where they *act* (the case, the review, the record). It was made deliberately distinct so the admin backstage never reads like the customer-facing questionnaire shell.

The personality is **professional, restrained, operationally precise** — quiet, durable, trustworthy. Density is a virtue here: desktop views stay information-rich (data tables, status pills, metric cards) while priority, state, and the next action stay obvious. Type is a single sans in heavy weights doing all the work; color is spent almost entirely on status and focus, never decoration. Mobile is a first-class operations path — every table has a stacked card fallback, nothing depends on desktop table widths.

This system explicitly rejects marketing-page composition: no oversized heroes, no decorative gradients, no glassmorphism, no playful illustrations, no inflated spacing, no nested cards. If a screen looks like a landing page, it is wrong.

**Key Characteristics:**
- Dark navigation shell + light evidence workspace (deliberate two-layer neutral system).
- Single accent (Teal Current) reserved for primary action, current selection, and state.
- One sans family, heavy weights (900 / 700), fixed rem scale — never fluid clamp.
- Flat-by-default surfaces: soft `shadow-sm` plus a signature white inner ring.
- Density with hierarchy; mobile stacks to cards, never horizontal scroll.

## 2. Colors

A cool, near-neutral palette carried by slate, with a single teal accent and a tight semantic vocabulary (emerald for healthy/online, rose for danger). Base tokens are authored as HSL custom properties on `:root` (`--brand`, `--foreground`, `--ink-muted`, …); most components consume Tailwind's `cyan` / `slate` / `emerald` / `rose` ramps that align to them. Hex values in the frontmatter are the canonical reference.

### Primary
- **Teal Current** (`#155e75`, cyan-800): Primary buttons and their pressed/hover deepening (`#164e63` → `#083344`). The single decisive action color in the light workspace.
- **Signal Cyan** (`#22d3ee`, cyan-400): The high-energy accent — active navigation item background (on the dark shell), the 3px focus outline, and the highlight-pulse used to draw the eye to a just-changed row. Reserved for "right here, right now."
- **Link Cyan** (`#0e7490`, cyan-700): Inline links and selected-tab text; `cyan-800`/`cyan-900` on hover.
- **Brand Base** (`#0891b2`, cyan-600): The `--brand` token; also the faint radial wash at the top of the body background.

### Neutral — the Console (dark shell)
- **Console Ink** (`#020617`, slate-950): Sidebar and mobile-header background; the darkest surface, the frame around everything.
- **Console Surface** (`#0f172a`, slate-900): Raised panels inside the dark shell (status card, hover states on nav).
- **Console Border** (`#1e293b`, slate-800): Dividers and hairlines within the dark shell.

### Neutral — the Workspace (light)
- **Content BG** (`#f1f5f9`, slate-100): The workspace canvas behind all panels.
- **Panel Surface** (`#ffffff`): Cards, tables, panels, inputs — the working surface.
- **Ink** (`#020617`, slate-950): Headings and primary emphasis text.
- **Ink Body** (`#334155`, slate-700): Table cells and default body text.
- **Ink Muted** (`#475569`, slate-600): Subtitles, secondary labels, placeholder text (`--ink-muted`).
- **Hairline** (`#e2e8f0`, slate-200): Panel borders, table row separators, dividers.

### Tertiary — semantic status
- **Success / Online** (`#059669` emerald-600, `#10b981` emerald-500): Healthy state, "系统运行中" dot, positive outcome signals.
- **Danger** (`#be123c`, rose-700 with `#fecdd3` rose-200 hairline): Destructive actions (soft-delete) and error state only.

### Named Rules
**The Two-Layer Rule.** Navigation is dark, work is light. Never invert it — no light sidebar, no dark content panels. The contrast between shell and workspace is how the operator stays oriented.

**The Signal Rule.** Teal/cyan appears on ≤10% of any workspace screen and only ever means *action, current selection, or live state*. The moment it decorates a heading or a divider, it stops being a signal.

**The Muted-Floor Rule.** Body text never drops below Ink Muted (`#475569`) on white; placeholder text uses the same value, not a lighter gray. Elegance is not worth failing 4.5:1.

## 3. Typography

**Body Font:** System sans stack — `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`
**Display / Label Font:** Same family. One family carries headings, buttons, labels, data.

**Character:** A single native sans, tuned per-OS, with Chinese resolving to PingFang SC / Microsoft YaHei. Hierarchy comes entirely from **weight and size**, not from a second typeface. The system leans on heavy weights (black 900 for page and section headings, bold 700 for panel titles and labels) against medium-weight body — a confident, no-frills operational voice.

### Hierarchy
- **Display / Page heading** (900, `1.5rem`→`1.875rem` responsive, line-height `2rem`, tracking `-0.025em`): The one big label at the top of a page. Fixed steps, never fluid clamp.
- **Title / Panel heading** (700, `1rem`, line-height `1.5rem`): Panel and card titles.
- **Body** (500, `0.875rem`, line-height `1.5rem`): Table cells, descriptions, form values. Prose capped at ~75ch (`max-w-[75ch]`).
- **Label / Eyebrow** (700, `0.75rem`, line-height `1rem`): Section labels, status-pill text, table headers (often `font-black` in `thead`).

### Named Rules
**The Fixed-Scale Rule.** Product type is a fixed rem scale, never `clamp()`. A heading that shrinks inside a sidebar looks broken, not responsive.

**The Weight-Not-Face Rule.** All hierarchy is weight + size within one family. Never introduce a display or serif face for flavor in labels, buttons, or data.

## 4. Elevation

Flat by default. Surfaces sit on the canvas with a soft `shadow-sm` and a **signature `ring-1 ring-white`** — a 1px white inner ring that crisps the top edge of every white panel against the slate canvas, reading as a subtle lift without a heavy drop shadow. Depth is carried more by the light/dark two-layer contrast and by hairline borders than by shadows. There is no elevated modal-heavy vocabulary; the console is calm and mostly single-plane.

### Shadow Vocabulary
- **Panel rest** (`box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05)` — Tailwind `shadow-sm`, plus `ring-1 ring-white`): Every panel, card, table shell, metric card.
- **Inset control** (`box-shadow: inset 0 2px 4px 0 rgb(0 0 0 / 0.05)` — `shadow-inner`): The segmented control track, to seat the active pill.
- **Focus outline** (`outline: 3px solid rgb(34 211 238 / 0.55); outline-offset: 2px`): The single global focus treatment — a Signal Cyan ring on every `:focus-visible`.
- **Highlight pulse** (`0 0 0 2px rgba(8,145,178,0.45)` fading to transparent over 2s): A one-shot cyan glow to spotlight a freshly-arrived or freshly-changed row.

### Named Rule
**The Flat-Ring Rule.** Surfaces are flat at rest — `shadow-sm` + white ring, nothing heavier. Stronger emphasis comes from state (hover border shift, focus outline, highlight pulse), never from stacking bigger shadows.

## 5. Components

### Buttons
- **Shape:** Rounded (`8px`, `rounded-lg`), min-height 44px (primary) / 40px (secondary & danger) for tap targets.
- **Primary:** Solid Teal Current (`#155e75`) on white text, `font-bold`, `px-4`. Hover deepens to `#164e63`, active to `#083344`; 200ms color transition.
- **Secondary:** White surface, `#e2e8f0` border, slate-700 text. Hover shifts border to cyan-200 and text to cyan-800 — a restrained lean toward the accent, not a fill.
- **Danger:** White surface, rose-200 border, rose-700 text; hover fills to rose-50. Used only for destructive (soft-delete) actions.
- **Disabled:** `opacity-50`, pointer events off. Never a full-saturation accent on a disabled control.

### Inputs / Fields
- **Style:** White surface, `#cbd5e1` (slate-300) border, `8px` radius, 44px tall, medium-weight text.
- **Placeholder:** slate-500 at normal weight — never lighter (Muted-Floor Rule).
- **Hover / Focus:** Border darkens on hover; on focus the border becomes cyan-700 with a soft `ring-2 ring-cyan-100` halo.

### Cards / Panels
- **Corner Style:** `12px` (`rounded-xl`) — one step softer than buttons/inputs.
- **Background:** White on the slate-100 canvas.
- **Elevation:** `shadow-sm` + `ring-1 ring-white` (Flat-Ring Rule). Never nest a card inside a card.
- **Header:** Optional tinted header band (`bg-slate-100/70`) or clean white header, separated by a hairline.
- **Internal Padding:** `16px` mobile → `20px` (`sm:p-5`) desktop.
- **Metric card:** Same shell; hover shifts border to cyan-300 to signal interactivity.

### Navigation
- **Style:** Dark shell — fixed 17rem left sidebar on desktop (`lg`), sticky dark top bar with horizontal-scroll nav on mobile.
- **Brand mark:** A cyan-400 rounded square with a black "R", beside "燃人AI 1.0".
- **Item — default:** slate-400 text on slate-950; icon slate-500. Hover: faint white surface (`white/[0.04]`), white text, cyan-300 icon.
- **Item — active:** a soft raised surface (`white/[0.06]` + inset white ring), white text, a cyan-tinted icon chip, and a short **Signal Cyan indicator bar** on the left edge (a rounded element, never a `border-left` stripe) — marking exactly one location without shouting.
- **Footer:** A "系统运行中" status card with an emerald pulse dot and version line.

### Data Table
- **Shell:** `rounded-xl` white with `shadow-sm` + white ring; header row on slate-100 with `font-black` slate-700 labels.
- **Rows:** Hairline `slate-100` separators, top-aligned slate-700 cells, hover tint `cyan-50/40`.
- **Mobile:** Below `md`, the table is replaced by a stacked `mobile-record-card` list — never a horizontally-scrolling table.

### Status Pill
- **Style:** `rounded-full`, bordered, `text-xs font-bold`, `px-2.5 py-1`. Color pairs (border + tint + text) map to state (emerald = healthy, rose = risk/danger, slate = neutral). Max-width safe with truncation.

### Segmented Control
- **Style:** `rounded-lg` slate-100 track with `shadow-inner`; items `rounded-md`, slate-600 bold. Active item lifts to a white surface with `shadow-sm` and cyan-800 text.

### Empty State
- Dashed slate-300 border, slate-100/70 fill, centered medium slate-600 text. Teaches the surface — not a bare "nothing here."

## 6. Do's and Don'ts

### Do:
- **Do** keep navigation dark (slate-950 + cyan accents) and the workspace light (slate-100 + white panels) — the Two-Layer Rule is the identity.
- **Do** spend Teal Current / Signal Cyan only on primary action, current selection, and live state (≤10% of a screen).
- **Do** use one sans family with weight + size for all hierarchy; fixed rem scale, never `clamp()`.
- **Do** keep body and placeholder text at Ink Muted (`#475569`) or darker on white — hit ≥4.5:1.
- **Do** give every table a stacked mobile card fallback; wrap, stack, stay tappable (~44px) at 375px.
- **Do** rest surfaces flat with `shadow-sm` + `ring-1 ring-white`; express emphasis through state, not bigger shadows.
- **Do** keep every interactive component's full state set (hover, focus-visible, active, disabled, loading) — never ship half.

### Don't:
- **Don't** import marketing-page composition — no oversized hero sections, no decorative gradients, no inflated spacing.
- **Don't** use glassmorphism (blur/glass cards) or playful illustrations; this is an operations console.
- **Don't** nest cards inside cards, or place a dark panel inside the light workspace.
- **Don't** use color or full-saturation accents on inactive/disabled states, or as decoration on headings and dividers.
- **Don't** use `border-left`/`border-right` greater than 1px as a colored accent stripe — use full borders, background tints, or leading icons.
- **Don't** use gradient text (`background-clip: text`) — emphasis comes from weight and size.
- **Don't** introduce a display or serif face for UI labels, buttons, or data.
- **Don't** let a mobile layout depend on desktop table widths or cause horizontal overflow.
- **Don't** change API calls, data contracts, or the questionnaire shell isolation in the name of visual polish.
