# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers and technical users who build and manage web automation pipelines. They are comfortable with flow logic, variables, selectors, and scripting. They are task-focused and efficiency-oriented, and they evaluate tools by how little the UI gets in the way of the work. They use Easy RPA on macOS or Windows alongside code editors and terminal windows.

## Product Purpose

Help technical users create, run, and diagnose Web automation flows in Studio, then schedule and monitor them through the supporting panels. A user should be able to diagnose and fix a routine failure in under a minute; this is a design goal, not a measured result.

## Positioning

A local-first desktop workbench for technical users, with visible execution evidence and explicit control over browser actions. AI assistance operates through validated tools and acceptance checks.

## Operating Context

Users work on macOS or Windows alongside code editors and terminals. Studio is the primary work surface; Dashboard, Scheduler, Task Center, and Settings support ongoing operation.

## Capabilities and Constraints

Use [the user guide](docs/OVERVIEW.md) for supported operations, execution-channel selection, authentication requirements, and capability boundaries. Component designs must not imply broader support.

## Brand Commitments

- Product name: Easy RPA.
- Personality: precise, professional, and capable.
- Maintain the character of a serious developer tool; avoid dated enterprise-suite chrome, soft consumer-SaaS conventions, and generic AI-product tropes.
- Make only claims supported by the product or evidence on hand. Never invent customer proof, performance results, pricing, licensing, or deployment claims.

## Evidence on Hand

- [The development guide](docs/TECHNICAL.md) documents architecture and verification.
- The repository implementation provides demonstrable product behavior across Studio, Dashboard, Scheduler, Task Center, and Settings.
- Existing identity and interface assets include `DESIGN.md`, `src/assets/app-icon.png`, and the application icon set under `buildResources/`.
- No public customer testimonials, case studies, press coverage, or quantified outcome data are currently available.

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
