---
name: Easy RPA
description: Desktop automation platform for developers — precise, capable, built to disappear into the workflow.
colors:
  indigo: "#6366f1"
  indigo-strong: "#2563eb"
  indigo-press: "#1d4ed8"
  indigo-soft: "rgba(99,102,241,0.10)"
  indigo-line: "rgba(99,102,241,0.38)"
  brand-gradient: "linear-gradient(90deg, #6366f1, #2563eb)"
  paper: "#f8fafc"
  paper-sunk: "#f1f5f9"
  ink: "#0f172a"
  ink-2: "#334155"
  ink-3: "#64748b"
  ink-4: "#94a3b8"
  rule: "rgba(15,23,42,0.08)"
  rule-2: "rgba(15,23,42,0.14)"
  canvas: "#f8fafc"
  surface: "#ffffff"
  live: "#3b82f6"
  live-soft: "rgba(59,130,246,0.12)"
  semantic-running-border: "#93c5fd"
  semantic-running-surface: "#eff6ff"
  semantic-success-border: "#bbf7d0"
  semantic-success-surface: "#f0fdf4"
  semantic-warning-border: "#fde68a"
  semantic-warning-surface: "#fffbeb"
  semantic-error-border: "#fecaca"
  semantic-error-surface: "#fff1f2"
  node-browser: "#2563eb"
  node-browser-pill: "#dbeafe"
  node-browser-text: "#1d4ed8"
  node-excel: "#16a34a"
  node-excel-pill: "#dcfce7"
  node-excel-text: "#15803d"
  node-ui: "#7c3aed"
  node-ui-pill: "#ede9fe"
  node-ui-text: "#6d28d9"
  node-file: "#ea580c"
  node-file-pill: "#ffedd5"
  node-file-text: "#c2410c"
  node-data: "#0891b2"
  node-data-pill: "#cffafe"
  node-data-text: "#0e7490"
  node-script: "#475569"
  node-script-pill: "#f1f5f9"
  node-script-text: "#1e293b"
  node-control: "#dc2626"
  node-control-pill: "#ffe4e6"
  node-control-text: "#b91c1c"
  node-variable: "#4f46e5"
  node-variable-pill: "#e0e7ff"
  node-variable-text: "#4338ca"
  node-browser-border: "#dbeafe"
  node-excel-border: "#a7f3d0"
  node-ui-border: "#fbcfe8"
  node-file-border: "#fed7aa"
  node-data-border: "#a5f3fc"
  node-script-border: "#e2e8f0"
  node-control-border: "#fecaca"
  node-variable-border: "#c7d2fe"
  edge-loop: "#f59e0b"
  json-bg: "oklch(0.175 0.018 264)"
  json-key: "oklch(0.74 0.035 264)"
  json-punct: "oklch(0.78 0.04 264)"
  json-string: "oklch(0.80 0.13 152)"
  json-number: "oklch(0.80 0.14 78)"
  json-muted: "oklch(0.56 0.02 264)"
  json-index: "oklch(0.72 0.05 264)"
  json-dim: "oklch(0.50 0.02 264)"
  json-accent: "oklch(0.62 0.10 264)"
typography:
  display:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.4
  caption:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "10px"
    fontWeight: 400
    lineHeight: 1.4
  micro:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "9px"
    fontWeight: 400
    lineHeight: 1.3
  badge:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "8px"
    fontWeight: 600
    lineHeight: 1
  figure:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.02em"
  doc-h1:
    fontFamily: "Inter Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  data:
    fontFamily: "JetBrains Mono Variable, Fira Code, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.4
  section-label:
    fontFamily: "JetBrains Mono Variable, Fira Code, Menlo, monospace"
    fontSize: "9.5px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "0.1em"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "14px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "20px"
  xl: "40px"
  2xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.brand-gradient}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "6px 10px"
    height: "28px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.indigo-strong}"
    borderColor: "{colors.indigo-line}"
    rounded: "{rounded.md}"
    height: "28px"
  button-outline:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    borderColor: "{colors.rule-2}"
    rounded: "{rounded.md}"
    padding: "6px 10px"
    height: "28px"
  card-default:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.rule}"
    rounded: "{rounded.lg}"
    shadow: "0 1px 3px rgba(15,23,42,0.06), 0 1px 2px rgba(15,23,42,0.04)"
    padding: "20px"
  flow-node:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.xl}"
    shadow: "0 1px 3px rgba(15,23,42,0.06), 0 1px 2px rgba(15,23,42,0.04)"
    width: "240px"
  nav-item-active:
    backgroundColor: "{colors.indigo-soft}"
    textColor: "{colors.indigo-strong}"
    rounded: "{rounded.sm}"
    height: "36px"
  nav-item-default:
    backgroundColor: "transparent"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.sm}"
    height: "36px"
---

# Design System: Easy RPA

## 1. Overview

**Creative North Star: "The precise-friendly automation tool"**

Easy RPA is a desktop flow-orchestration tool, and it should feel like one: clean white panels on a cool slate field, an honest information density, and color used as semantics rather than decoration. The visual register sits with Figma's property panel, Linear's surfaces, and Raycast — serious tools that disappear into the work — but it is **warmer and friendlier than an austere terminal**: soft layered shadows give cards real depth, corners are gently rounded (12–14px on panels and nodes), and the eight node-kind colors make the canvas legible at a glance.

The chrome is **all-light**: a white NavRail (hairline right edge) frames a slate-50 (`#f8fafc`) app field on which white content panels and flow nodes sit with soft shadows. The brand is an **Indigo → Blue** pairing: `--color-accent` (indigo-500 `#6366f1`) is the brand fill — primary-button gradient, selection rings, the brand mark — while `--color-accent-strong` (blue-600 `#2563eb`) is the AA-safe text/active register for links, active tabs, and accent text on paper. The **live/running** signal is a deliberately distinct blue (`#3b82f6`), held apart from the indigo brand so "the system is executing" never reads the same as "this is selected," and always reinforced by motion (the shimmer status bar, the breathing live dot, flowing edges).

Personality is carried by **a single well-tuned sans (Inter)** across all UI, **JetBrains Mono** for machine data (selectors, URLs, paths, timestamps, counts), and the **rationed indigo brand + eight semantic node colors** — not by an editorial display face. The earlier "Ledger" editorial language (system serif figures, hairline-only bands, mono-uppercase micro-labels, dark NavRail) has been **fully retired**; it read as a magazine, not a tool.

This system explicitly rejects: the generic enterprise dashboard (icon-overloaded, padded blue headers); the SaaS-pastel tool (bubble flows, friendly-rounded everything); the orange/teal AI-dashboard aesthetic (gradient accents on every metric, glass cards, ambient glow). Those signal "a designer touched this." Easy RPA signals "this works."

**Key Characteristics:**
- Slate-ink-on-paper near-monochrome chrome; indigo is the sole *interactive* accent. Semantic color is separate and reserved for meaning: the live blue for execution, the eight node-kind hues on canvas
- Two type registers: **Inter** (all UI prose and data figures), **JetBrains Mono** (machine data — selectors, paths, IDs, timestamps). No serif, no display face
- 1px hairline rules (`--color-rule` / `--color-rule-2`) and single-row stat bands replace rounded accent cards
- Light NavRail (white, hairline right border) frames a slate-50 canvas — all-chrome is light; indigo accent-soft fills active items, accent-strong colors active text
- Border-based elevation; soft layered shadows on cards and nodes; box-shadows reserved for overlays only
- All tokens are defined once in `src/styles.css` `@theme` and surfaced as Tailwind utilities — the single source of truth

---

## 2. Colors: Slate ink, soft surfaces, Indigo→Blue brand

A clean slate-neutral system with a rationed Indigo→Blue brand and a full semantic-state vocabulary. The brand appears on primary actions, selection, and active state; the live blue marks execution; the eight node-kind colors encode data on the canvas.

### The Brand — Indigo → Blue

- **Brand Indigo** (`#6366f1`): The brand fill — primary-button gradient start, selection rings, focus, the brand mark. `--color-accent`.
- **Brand Blue Strong** (`#2563eb`): AA-safe text/active register (~5.2:1 on white) — links, active tabs, accent text on paper, gradient end. `--color-accent-strong`.
- **Brand Blue Press** (`#1d4ed8`): Pressed / active. `--color-accent-press`.
- **Brand Indigo Bright** (`#818cf8`): On-dark register (overlays, console). `--color-accent-bright`.
- **Soft / Wash / Line** (`--color-accent-soft` 10%, `--color-accent-wash` 5%, `--color-accent-line` 38%): tinted fills, faint hover fields, accent hairlines.
- **Brand gradient** (`linear-gradient(90deg, #6366f1, #2563eb)`): primary CTAs and progress fills. `--gradient-brand` / `.bg-brand-gradient`.

### The Live signal — Blue (distinct from brand)

- **Live** (`#3b82f6`) / **Live Soft** (12%) / **Live Line** (45%): the running/executing signal. A true blue held apart from the indigo brand so running state never reads as "selected." Always reinforced by motion (`.bg-running-strip` shimmer, `.live-dot`, `running-glow`, flowing edges). `--color-live`.

### Slate ink & paper (Neutral)

- **Paper** (`#f8fafc`) / **Paper Sunk** (`#f1f5f9`): the app/canvas field and recessed wells / hover fill / table zebra. Cool slate, never cream.
- **Ink** (`#0f172a`): titles, headings, primary text — slate-900.
- **Ink-2** (`#334155`, 8.9:1) / **Ink-3** (`#64748b`, 4.6:1, clears AA body) / **Ink-4** (`#94a3b8`, non-text only — placeholders, rest icons, decoration).
- **Rule** (8%) / **Rule-2** (14%): the 1px hairlines that bound panels alongside soft shadows.
- **Canvas** (`#f8fafc`) / **Surface** (`#ffffff`): app/flow field and content panels.

### Semantic States

States use matched border + background surface pairs. Color alone is never the only signal; icon and label always accompany.

| State             | Ring / Border            | Surface                         | Text          |
| ----------------- | ------------------------ | ------------------------------- | ------------- |
| Live / Running    | `--color-live-line` (45%) | `--color-live-soft` (12%)      | `#1d4ed8`     |
| Success           | `emerald-200`            | `emerald-50`                    | `emerald-700` |
| Warning / Stopped | `amber-200`              | `amber-50`                      | `amber-800`   |
| Error             | `red-200`                | `red-50`                        | `red-600`     |
| Pending / Skipped | `slate-200/70`           | `slate-100`                     | `slate-500`   |

Running state gains the `running-glow` animated border and the `bg-running-strip animate-shimmer` top bar — a live-blue shimmer that signals execution at the layout level. Every state pairs its color with an icon **and** a Chinese label; color alone never carries the meaning.

### Node-Kind Taxonomy

The Studio canvas color-codes node kinds — a deliberate functional palette distinct from the chrome accent. These are data encoding, not decoration, and are the one place a fuller palette is intentional. Each kind carries an `accent` (icon / bar), a `pill` fill, and a `text` color; **every pill/text pair clears 4.5:1**.

| Kind       | Accent    | Pill      | Text      |
| ---------- | --------- | --------- | --------- |
| `browser`  | `#2563eb` | `#dbeafe` | `#1d4ed8` |
| `excel`    | `#16a34a` | `#dcfce7` | `#15803d` |
| `ui`       | `#7c3aed` | `#ede9fe` | `#6d28d9` |
| `file`     | `#ea580c` | `#ffedd5` | `#c2410c` |
| `data`     | `#0891b2` | `#cffafe` | `#0e7490` |
| `script`   | `#475569` | `#f1f5f9` | `#1e293b` |
| `control`  | `#dc2626` | `#ffe4e6` | `#b91c1c` |
| `variable` | `#4f46e5` | `#e0e7ff` | `#4338ca` |

Canvas edges: selected uses the brand indigo `#6366f1`; a loop edge uses dashed amber `#f59e0b`; a running edge uses the live blue.

### Named Rules

**The Brand Scarcity Rule.** The indigo brand is the *interactive* color, not a decorative one. It appears on primary / selected / active affordances and never as a background pattern or accent without a semantic reason. When in doubt, use ink.

**The Warning Differentiation Rule.** Indigo is the brand and blue is the live/running color. Warning and Stopped states use amber/yellow (`#d97706` family) to stay visually distinct from both. Never use indigo or blue for warning states.

---

## 3. Typography

**Primary Font:** Inter Variable (PingFang SC, system-ui, sans-serif)
**Mono Font:** JetBrains Mono Variable (Fira Code, Menlo, monospace)

**Character:** A single well-tuned sans handles all prose hierarchy without pairing; JetBrains Mono activates for a distinct register: numbers, technical labels, timestamps, selectors, paths, code. The switch from Inter to Mono is semantic, not stylistic. There is no display/serif face — data figures are Inter bold with `tabular-nums` (`.figure`).

### Hierarchy

- **Page Title** (Semibold 600, 14px, `tracking: -0.02em`): WorkspaceShell sticky header — compact desktop toolbar style.
- **Headline** (Semibold 600, 16px, `tracking: -0.01em`): Dialog titles, section subheadings.
- **Title** (Semibold 600, 13px): Card titles, list group headers.
- **Body** (Regular 400, 12px, `line-height: 1.5`): Table rows, descriptions, log entries, tooltips. Cap at ~80ch.
- **Section Label** (Mono Regular 400, 9.5px, `letter-spacing: 0.1em`, ALL CAPS, `text-slate-400`): Panel section headers. The signature micro-typography of precision tools.
- **Data Mono** (Mono Bold 700, 40px for hero metrics; varies 12–32px for secondary): Statistics, queue counts, elapsed time. Always mono, always tabular-nums.

### Named Rules

**The Mono-Is-Data Rule.** If the value is a number, timestamp, version string, step count, or technical identifier — it uses JetBrains Mono with `tabular-nums`. No exceptions.

**The Uppercase Label Rule.** Section panel headers use: `font-mono text-[9.5px] uppercase tracking-[0.1em] text-slate-400`. Not `font-semibold text-[12px] text-slate-600`. The bold-prominent style is a web-app pattern. The small-mono-uppercase style is a precision tool pattern.

---

## 4. Elevation

Easy RPA uses **soft, layered shadows + a 1px hairline border** for elevation. Cards, panels, and flow nodes get real, gentle depth (the RPA register, not the austere terminal). Depth is conveyed through:

1. **Soft shadow**: `--shadow-sm` (`0 1px 3px / 0 1px 2px` slate) at rest on cards/nodes; `--shadow-md` on hover.
2. **Background luminance step**: Canvas (`#f8fafc`) → Surface (`#ffffff`).
3. **1px hairline border**: `border-rule` on content panels and nodes.
4. **Light NavRail**: white with a hairline right edge — chrome is all-light.

### Shadow Vocabulary

- **Overlay shadow** (`0 8px 32px rgba(15,23,42,0.10), 0 2px 8px rgba(15,23,42,0.06)`): Dialogs, popovers, dropdown menus.
- **Node canvas shadow** (`0 2px 8px rgba(15,23,42,0.07), 0 1px 3px rgba(15,23,42,0.05)`): React Flow nodes.
- **Node selected ring** (`--shadow-node-selected`: `0 0 0 3px rgba(99,102,241,0.22), 0 4px 20px rgba(15,23,42,0.12)`): brand-indigo selection ring on the flow canvas.
- **Running glow** (`running-glow` class): Animated indigo gradient border applied to StatusTile and running nodes. The signature live-execution signal.

### Named Rules

**The Soft-Shadow Rule.** Content panels, cards, and flow nodes carry `--shadow-sm` at rest and escalate to `--shadow-md` on hover, paired with a `border-rule` hairline. Ambient *glow* (large colored blur) is still reserved for the running state only.

**The No-Hover-Lift Rule.** Cards and nodes do not animate on hover with `translate-y`. Hover = shadow escalation + border darkening only. Hover-lift is a marketing-page pattern; desktop tool panels stay put.

---

## 5. Components

### Page Header (WorkspaceShell)

The sticky header bar anchors every workspace page. It signals desktop-app chrome, not web content.

- **Height:** 48px (`h-12`), fixed — never grows with content; aligns with the NavRail brand row rule
- **Background:** `bg-surface` with `border-b border-rule`
- **Title:** 14px, Semibold, `text-ink`, leading-none — tight, authoritative
- **Description:** 12px, `text-ink-3`, leading-none — one line, inline after the title on the same baseline
- **Actions:** trailing edge of the header row (refresh, primary page actions)
- **Content field:** full width, `px-6 pt-5 pb-10`, vertical rhythm `gap-5` — no centered max-width column
- **Stat strip (StatBand):** one slim row (`min-h-11`) of inline figures — 11px label + 15px tabular value + mono note, hairline-divided; never a tile grid of hero metrics

### Buttons

- **Primary:** brand gradient (`bg-brand-gradient`, indigo→blue) background, white text, `height: 28px`. `hover:opacity-90`. Primary CTAs only: Run, Save, Confirm.
- **Secondary:** White background, indigo border (`border-accent-line`), indigo text (`text-accent-strong`). `hover:bg-accent-soft`. Secondary brand-adjacent actions.
- **Soft:** accent-wash fill + accent-line border + accent-strong text. Brand-adjacent actions that shouldn't read as primary.
- **Outline:** White background, `border border-slate-200`, Ink Secondary text. Neutral secondary actions.
- **Subtle:** `border-rule-2` + surface fill + `text-ink-2`. The neutral workhorse on operational surfaces (refresh, import, row actions).
- **Ink:** solid ink fill, paper text. Rare — high-emphasis neutral confirmation.
- **Danger:** Red background, white text. Stop and delete only.
- **Ghost:** No border, no background. Ink Muted text. Toolbar icon-buttons, inline row actions.
- **Focus ring:** `outline: 2px solid var(--color-accent-line)`, `outline-offset: 2px`.
- **`:active`:** `scale(0.98)` — physical press simulation.

### Cards / Content Panels

- **Corner Style:** `rounded-xl` (12px)
- **Background:** Surface white (`#ffffff`)
- **Shadow:** `shadow-[0_1px_4px_rgba(15,23,42,0.06)]` on stat tiles only; none on other content panels
- **Border:** `border border-slate-200/70` at rest
- **Internal Padding:** `p-5` (20px)
- **Hover:** `hover:bg-slate-50/60` — no translate, no shadow escalation
- **Semantic variants:** StatusTile shifts background + border + gains `running-glow` when executing.

### Inputs / Fields

- **Style:** `rounded-md` (6px), `border border-slate-200`, `bg-white`, 12px text, `height: 32px`
- **Focus:** `focus-visible:border-accent-line focus-visible:ring-2 focus-visible:ring-accent-soft`（注意是三个独立类，连写成 `border-accent-linefocus:ring-2` 会让 Tailwind 整条失效且不报错）
- **Error:** `border-red-400`, `ring-red-200`
- **Disabled:** `opacity-50 cursor-not-allowed bg-slate-50`

### Navigation (NavRail)

- **Background:** White (`#ffffff`) with `border-r border-rule` hairline — all-light chrome, not dark sidebar
- **Width:** 176px expanded, 48px collapsed; 250ms ease-out transition
- **Nav item (default):** `text-ink-3`, transparent background, `rounded-md`, `height: 36px`
- **Nav item (hover):** `bg-paper-sunk`, `text-ink-2`
- **Nav item (active):** `bg-accent-soft`, `text-accent-strong`, left-edge `w-0.5 h-4 rounded-r-full bg-accent` accent line
- **Brand mark:** brand-gradient square `h-6 w-6 rounded-lg` with Zap icon — signature brand moment on light chrome
- **Collapse toggle:** Ghost icon-button, `text-ink-3`, `hover:bg-paper-sunk`

### Section Labels (PanelSection)

- Always: `font-mono text-[9.5px] uppercase tracking-[0.1em] text-slate-500`
- Always preceded by a small icon: Lucide, 14px, `strokeWidth={1.5}`, `text-slate-400`（图标是装饰性重复，可用更浅的一档）
- Never: bold, `text-slate-600`, larger than 11px, sentence case
- `text-slate-400`（2.56:1）不可用于任何承载文字的元素——它只够画图标

### Operational surface primitives (`workspace/surfaces.tsx`)

One small set of primitives carries every non-canvas page (Dashboard, Task Center, Scheduler, Statistics, Permissions). There is deliberately **no hero-metric tile** — a desktop tool reports state in a status strip, not a KPI grid.

- **`StatBand`:** one slim row (`min-h-11`), hairline-divided, `rounded-lg border border-rule bg-surface shadow-xs`. Wraps `Figure` children.
- **`Figure`:** 11px label + 15px `.figure` value (Inter Bold, `tabular-nums`, `tracking -0.02em`) + optional 10px mono note. `tone="live"` colors a non-zero value with the live blue.
- **`Panel`:** white card, `rounded-lg border border-rule shadow-xs`, icon + section label header. Never nested inside another Panel.
- **`KeyRow`:** label / value pair on one hairline-separated row. `mono` prop switches the value to JetBrains Mono.
- **`StateTag`:** semantic dot + label. Color is **always** paired with the text label (colorblind-safe). States: `live` (breathing dot) / `success` / `warning` / `error` / `idle`.
- **`SurfaceEmpty`:** centered 11px `text-ink-3` empty state.

### Flow Node Status (Canvas — Signature)

The canvas is the product's hero surface; node state must read at a glance from across the graph. Each `RpaStepNode` carries TWO reinforcing status signals:

- **Status bar:** a full-width `h-[3px]` bar pinned to the node's top edge, color = state. Running uses `bg-running-strip animate-shimmer` (flowing live blue); done emerald, error red, pending/skipped slate. This is the across-the-room signal.
- **Status pill:** an explicit `icon + 中文标签` pill on the right of the node body (`运行中 / 完成 / 失败 / 待运行 / 跳过`), tinted to the state with a `ring-1` outline. Color is always paired with icon + label (colorblind-safe).
- **Running emphasis:** the running node additionally gets the `running-glow` animated border + `shadow-running`; the edge into a running node gets `edge-running` (a flowing dashed Indigo stroke). Live execution should be the most alive thing on screen.
- **Selected:** `shadow-(--shadow-node-selected)` (Indigo focus ring), never a color change that competes with status.

---

## 6. Do's and Don'ts

### Do:

- **Do** use `border border-slate-200/70` for inline content panel boundaries. The border is the elevation signal; ambient shadow is not.
- **Do** use JetBrains Mono + `tabular-nums` for every number that can change dynamically.
- **Do** use `font-mono text-[9.5px] uppercase tracking-[0.1em] text-slate-500` for section panel labels. Every time.
- **Do** apply `running-glow` to the StatusTile and animated nodes when `runtimeStatus === 'running'`. The animated border IS the live signal.
- **Do** shift the StatusTile's background AND border when runtimeStatus changes. Text-color change alone is insufficient.
- **Do** keep the brand indigo (`#6366f1`) rare: primary buttons, active nav items, selection only. Running state uses the live blue (`#3b82f6`), never the brand.
- **Do** give every semantic state a non-color differentiator — icon and label alongside color.
- **Do** use `rounded-xl` (12px) for content panels and `rounded-lg` (8px) for controls.
- **Do** use `hover:bg-slate-50/60` on cards. A barely perceptible background shift is the hover signal.
- **Do** honor `prefers-reduced-motion` for all animations including `running-glow` and `pulse-ring`.
- **Do** use left-edge accent lines (`w-0.5 h-4 rounded-r-full`) on active nav items to reinforce selection state.

### Don't:

- **Don't** use `rounded-2xl` on content panels. `rounded-xl` for panels, `rounded-lg` for controls.
- **Don't** escalate elevation on content panels. `shadow-xs` on `Panel` / `StatBand` is the ceiling; deeper shadows are reserved for overlays (dialog, dropdown, floating AI panel).
- **Don't** animate cards with `hover:-translate-y-0.5`. Cards don't levitate. Hover = background shift only.
- **Don't** use `font-semibold text-[12px] text-slate-600` for section panel labels.
- **Don't** scatter the brand indigo decoratively. If it's not active, selected, brand, or a primary action — it is the wrong color.
- **Don't** use indigo for warning/stopped states. Those use amber (`#d97706` family) to stay distinct from the brand.
- **Don't** look like **UiPath or Power Automate** — icon-overloaded, enterprise-blue chrome, dated toolbars.
- **Don't** look like **Zapier or Make** — SaaS-pastel, bubble flows, friendly rounded everything.
- **Don't** look like a **generic AI dashboard** — teal/orange/indigo gradients, glass cards, metric-number hero tiles with ambient glow.
- **Don't** look like **Notion or ClickUp** — document-first heavy sidebar. This is a flow editor.
- **Don't** use warm neutrals (cream, sand, parchment, bone) anywhere in the neutral surface stack.
- **Don't** use `divide-slate-50` for row dividers. Use `divide-[rgba(15,23,42,0.06)]`.
