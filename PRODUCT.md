# Product

## Register

product

## Users

Developers and technical users who build and manage web automation pipelines. They are comfortable with flow logic, variables, selectors, and scripting. They are task-focused and efficiency-oriented — they evaluate tools by how little the UI gets in the way of the work. They run this on macOS or Windows alongside code editors and terminal windows.

## Product Purpose

Easy RPA is a desktop Electron application for creating, running, and monitoring web automation flows. Users build visual node-based pipelines that automate browser interactions (clicking, filling forms, extracting data, navigating pages). The Studio canvas is the core work surface. Dashboard, Scheduler, Task Center, and Settings are operational surfaces for managing running and scheduled automations.

Success looks like: a developer can open a flow, run it, diagnose a failure, and fix it in under a minute. The UI never becomes the bottleneck.

## Brand Personality

Precise · Professional · Capable

The interface communicates that the system is doing complex things reliably. It earns trust through exactness — correct spacing, legible hierarchy, restrained use of color — not through friendliness or approachability. Think Figma's property panel, Raycast's command palette, VS Code's sidebar: serious tools that disappear into the workflow.

## Anti-references

- **UiPath / Power Automate** — generic enterprise blue, icon-overloaded, dated Windows-Forms-era layout. Heavy chrome that feels like overhead, not a tool.
- **Zapier / Make** — SaaS-pastel, friendly bubble UI, approachable-consumer aesthetic. Too soft for a developer tool.
- **Generic AI dashboards** — purple/indigo gradients, floating glass cards, ambient shadows on everything, "metric number hero" tiles. Looks designed, not functional.
- **Notion / ClickUp** — document-first, heavy text hierarchy, sidebar that competes with the content. This is a flow editor, not a workspace OS.

## Design Principles

1. **Precision over decoration.** Every visual element must earn its place. No ambient shadows for aesthetics, no inflated corner radii for approachability. The interface is calibrated, not adorned. When in doubt, remove rather than add.

2. **State-first surfaces.** The interface reflects live system state (running, error, queue depth, step count) through color, form, and layout — not just text labels. A tile that changes background when a flow is running is more useful than one that only changes a word.

3. **Native desktop language.** Design decisions belong in the same visual register as Figma's property panel, Linear, and VS Code. A calm light NavRail, tight spacing, 1px hairline borders, soft single-layer shadows, and Inter labels with JetBrains Mono reserved for technical data (selectors, versions, IDs). Not a web app wrapped in Electron.

4. **Restrained identity.** The brand runs Indigo `#6366f1` → Blue `#2563eb` (gradient for primary actions; Blue-600 for active states, links, and AA text). A distinct live blue `#3b82f6` marks running state so it never collides with the brand. Color appears in purposeful, semantic positions — active states, brand marks, run indicators — never as decoration. Signal through placement, not saturation.

5. **Density calibrated to expertise.** Target users are developers. Dense-but-legible beats spacious-but-shallow. Inter labels for section headers, tabular numerals for statistics, 12px body in dense list rows. (The earlier mono-uppercase "ledger" label voice has been retired — it read as editorial, not tool.) Every pixel freed from decoration goes to information.

## Accessibility & Inclusion

- Target: WCAG 2.1 AA minimum. AAA for body text contrast where achievable.
- Platform: Electron / Chromium on macOS and Windows. Full keyboard navigation expected by developer users.
- Reduced motion: respect `prefers-reduced-motion` for any transitions added.
- Color blind safe: semantic states (running/error/warning/success) must always have a non-color differentiator (icon or label) in addition to color.
