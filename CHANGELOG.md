# Changelog

## 0.5.0 — 2026-09-02

- Replaced the fixed two-column information body with a responsive, rectangular
  one/two/three-column Project Info grid and a project-persisted summary rail.
- Added project-owned Prompt Hold: one text clipboard snapshot with Copy, Clear,
  and optional Paste Into a live Codex/Terminal pane; no monitoring or history.
- Added persistent Project Commands with stable IDs, categories, descriptions,
  root targeting, manual CRUD, copy, visible-terminal execution, and conservative
  read-only suggestions from common project files.
- Added the project-specific Workspace Dock with persistent recursive horizontal/
  vertical splits, draggable ratios, provider state, pane titles/identity,
  menu-driven movement, dock/undock windows, and Focus mode that preserves the
  original tree.
- Generalized the VTE implementation into multiple reusable Terminal and Codex
  panes. Runtimes are isolated by stable project/pane ID and survive project
  switching; no PTY/process object is serialized.
- Changed the native CODEX action to focus a matching live embedded account/session
  pane or create one. Explicit + PANE → Codex creates another pane. The existing
  `codex-start` argv/cwd integration, dashboard, scrollback, mouse behavior,
  resizing, and right-click paste remain VTE-owned.
- Added lightweight Files panes for canonical/associated roots and optional
  WebKitGTK 6 Browser panes with minimal chrome and Open Externally. Missing
  WebKit or an unusable process sandbox disables only Browser panes.
- Migrated project persistence from schema 3 to schema 4 with a one-time
  `projects.json.v0.4.0.bak`, safe defaults, and unknown-field preservation.
- Added coverage for migration, per-project isolation, split/pane identity,
  Codex deduplication, runtime retention, dock/focus restoration, browser fallback,
  Prompt Hold clipboard reads, and visible-terminal command routing.

## 0.4.0 — 2026-08-28

- Added two-mode Add Project: register an existing directory or asynchronously
  clone any Git-compatible URL into a validated destination. Clone progress,
  captured output, cancellation, conflict handling, and registration are one
  transaction; failed clones never create project records.
- Added a real PTY-backed VTE terminal dock with a resizable lower pane,
  canonical/associated-root selection, explicit project-switch recreation, and
  the existing Tilix-first external-terminal fallback.
- Added safe project editing and registration-only removal. Renames and
  directory changes preserve stable session identity; removal never deletes
  files, Git data, sessions, handoffs, or history.
- Added labeled associated paths with extensible roles, optional shell access,
  optional required validation, Git/non-Git inspection, and concise
  READY/STATUS/COPY ALL integration.
- Added explicit Linux platform services and capability reporting around
  terminals, clipboard helpers, choosers, desktop integration, executable
  discovery, paths, folders, and URLs. Unsupported platforms now fail clearly.
  Architecture is prepared for future platform backends; Linux remains the
  supported runtime in v0.4.
- Migrated v0.3.1 registries automatically to schema 3 with empty associated
  paths, stable registration IDs, legacy external-terminal preferences,
  unknown-field preservation, and a one-time registry backup.
- Preserved the existing `codex-start` integration, project/session/handoff/
  resume model, account-intent-only behavior, GTK clipboard and chooser fixes,
  guarded stale-resistant COMMIT, and destination-first explicitly confirmed
  PUSH.

## 0.3.1 — 2026-08-27

- Fixed the Add Project folder button crash by moving directory selection to
  the GTK 4 asynchronous `FileDialog` API with an owned callback lifetime and
  recoverable cancellation/error handling.
- Made native GUI clipboard writes use the GTK/GDK display clipboard for
  reliable Wayland operation; CLI helper fallbacks remain optional and bounded.
- Made the command palette focus its empty search field when mapped.
- Made Escape dismiss the command palette without activating a command.

All other v0.3 workflows and safety guards remain unchanged.
