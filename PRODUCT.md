# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers and technical users who build and manage web automation pipelines. They are comfortable with flow logic, variables, selectors, and scripting. They are task-focused and efficiency-oriented, and they evaluate tools by how little the UI gets in the way of the work. They use Easy RPA on macOS or Windows alongside code editors and terminal windows.

## Product Purpose

Easy RPA is a local-first Electron desktop application for creating, running, and monitoring web automation flows. Users build visual node-based pipelines that automate browser interactions such as navigation, clicking, form filling, and data extraction. Studio is the core work surface; Dashboard, Scheduler, Task Center, and Settings support the operation of running and scheduled automations.

Success means a developer can open a flow, run it, diagnose a failure, and fix it in under a minute. The interface must not become the bottleneck.

## Positioning

Easy RPA is a local-first visual RPA tool for technical users. Its product mechanism combines two explicit browser execution channels: Playwright with a managed persistent profile for unattended automation, and a Chrome Extension that reuses the user's signed-in browser for SSO, real-session access, and human collaboration. AI assistance is constrained by structured tools, validation, and acceptance evidence rather than treated as an unconstrained chat layer.

## Operating Context

- Users design, test, run, and diagnose flows in Studio, then schedule and monitor operational runs through the supporting panels.
- Core project data, schedules, execution state, cache, and logs are stored locally under `~/.easy-rpa/` by default.
- Playwright is the default channel for repeatable unattended runs. The Chrome Extension channel is used when an existing signed-in Chrome session or human participation is required and is not the recommended unattended channel.
- The application runs as a web interface inside Electron/Chromium on macOS and Windows. Target websites and configured AI providers may still require network access.

## Capabilities and Constraints

- Visual node-based flows cover browser interaction, extraction, data transformation, files and Excel, scheduling, task monitoring, and AI-assisted workflow authoring.
- Automation scope is the web browser. Native desktop GUI automation is not supported.
- CAPTCHA bypass is not a product capability. Workflows that encounter a CAPTCHA require a legitimate human or site-supported path.
- Iframes and open shadow roots are supported automation contexts; closed shadow roots are outside the current technical boundary.
- Complex site-specific widgets such as arbitrary date ranges, multi-selects, and cascaded selectors are not assumed to have universal handling.
- The embedded Python execution environment uses a limited dependency allowlist rather than arbitrary third-party package installation.

## Brand Commitments

- Product name: Easy RPA.
- Personality: precise, professional, and capable.
- Earn trust through exact behavior, clear system state, and reliable diagnostics rather than decorative friendliness.
- Maintain the character of a serious developer tool; avoid dated enterprise-suite chrome, soft consumer-SaaS conventions, and generic AI-product tropes.
- Make only claims supported by the product or evidence on hand. Never invent customer proof, performance results, pricing, licensing, or deployment claims.

## Evidence on Hand

- `README.md` and `OVERVIEW.md` document the product scope, architecture, workflows, execution channels, and known boundaries.
- The repository implementation provides demonstrable product behavior across Studio, Dashboard, Scheduler, Task Center, and Settings.
- Existing identity and interface assets include `DESIGN.md`, `src/assets/app-icon.png`, and the application icon set under `buildResources/`.
- No public customer testimonials, case studies, press coverage, or quantified outcome data are currently available. Future work must not imply or fabricate them.

## Product Principles

1. **Keep the tool out of the way.** Favor efficient, expert workflows and information density that serves the task.
2. **Expose state and evidence.** Running, queued, failed, and completed work must be understandable and diagnosable without guesswork.
3. **Preserve local control.** Keep project data and execution observable and locally owned, and state clearly when a workflow crosses that boundary.
4. **Match the execution channel to the job.** Distinguish unattended automation from signed-in or human-assisted browser work instead of hiding their tradeoffs.
5. **Constrain automation and AI with verifiable outcomes.** Structured tools, validation, and acceptance evidence take precedence over confident but unsupported output.

## Accessibility & Inclusion

- Target WCAG 2.1 AA at minimum, with AAA body-text contrast where achievable.
- Support full keyboard navigation expected by developer users on macOS and Windows.
- Respect `prefers-reduced-motion` for transitions and animations.
- Give every semantic state, including running, error, warning, and success, a non-color differentiator such as an icon or label.
