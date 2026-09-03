# Architecture

```text
Project Registry ─ Project Workspace ─ Work Session Store ─ Settings / Activity
        │                  │                    │                    │
        └──────────────────┴────────────────────┴────────────────────┘
                                      │
                               Workbench service
 clone · edit/remove · roots · ready/status · project commands · prompt hold
     codex · commit/push · copy-all · handoff/resume · accounts · threads
                  │                    │                    │
           thin Python CLI      platform services   headless GUI controller
                                      │                    │
                                Linux backend      GTK4/libadwaita
                                                     │
                               recursive split dock / provider registry
                                Codex · Terminal · Browser · Files
```

## Module boundaries

- `models.py`: version-tolerant Project, AssociatedPath, WorkSession, handoff,
  and project-workspace attachment records.
- `workspace.py`: GTK-free ProjectCommand, WorkspacePane, ProjectWorkspace,
  and recursive SplitLayout models plus deterministic add/move/dock/focus
  operations.
- `workspace_service.py`: project-owned Prompt Hold, Project Commands, pane,
  layout, docking, and focus workflows mixed into the existing Workbench facade.
- `workspace_runtime.py`: runtime-only project/pane registry. PTYs, WebViews,
  GTK widgets, and process objects never enter persisted models.
- `command_discovery.py`: read-only suggestion-source protocol and conservative
  package.json, Makefile, justfile, pyproject.toml, Cargo.toml, and Compose
  sources. Discovery cannot persist or execute a command.
- `store.py` / `projects.py`: atomic, versioned project registry persistence.
- `sessions.py`: XDG session persistence, current pointer, and updates. Work
  Sessions remain separate from Project Workspace state.
- `clone.py`: cancellable generic Git clone transaction and destination safety.
- `associated.py`: canonical/associated-root resolution and live per-root Git state.
- `git.py`: repository inspection, effective/local identity, upstream state,
  diffs, and the Git process boundary.
- `github.py`: remote parsing and optional GitHub CLI identity hints.
- `codex.py`: adapter to the existing `codex-start` engine, status parsing,
  launch prompts, and account availability. It does not implement Codex.
- `terminal.py`: external-terminal policy plus argv/cwd-separated embedded shell
  and embedded command specifications.
- `clipboard.py`: replaceable CLI clipboard backends and capability fallback.
- `platform/base.py` / `platform/linux.py`: platform capabilities, executable
  discovery, open-folder/open-URL argv, launcher support, and Linux behavior.
- `preflight.py`: structured READY checks with pass/warn/fail levels.
- `context.py`: compact transferable project/session context.
- `handoff.py`: concise handoff documents and immutable handoff archives.
- `services.py`: application workflow facade used by all front ends.
- `cli.py`: argument parsing and human-readable result formatting only.
- `activity.py` / `settings.py`: bounded local history and small preferences.
- `transcripts.py`: replaceable, filename-based transcript discovery boundary.
- `desktop.py` / `desktop_entry.py`: platform-backed URL/folder opening and
  explicit user launcher install.
- `gui/state.py`: immutable display models with no GTK dependency.
- `gui/controller.py` / `gui/workspace_controller.py`: presentation controllers
  that call public Workbench workflows.
- `gui/clipboard.py`: main-thread one-shot GDK text reads and clipboard writes.
- `gui/terminal.py`: optional VTE capability detection, PTY sessions, scrolling,
  right-click paste, focus, liveness, and visible command feed.
- `gui/panels.py` / `gui/project_panels.py`: responsive Project Info grid,
  collapse rail, Prompt Hold, Commands, and related actions.
- `gui/dock.py`: split-tree renderer, persistent per-project runtime ownership,
  pane controls, dock/undock windows, Focus mode, and command-to-terminal routing.
- `gui/workspace.py`: provider contracts and Codex, Terminal, Browser, and Files
  surfaces.
- `gui/browser_runtime.py`: WebKitGTK and bubblewrap process-sandbox capability
  check, kept outside the standard-library-only core.
- `gui/window.py` / `gui/dialogs.py` / `gui/widgets.py`: native shell and existing
  interaction layer.
- `gui/resources/*.css`: compact dark workstation visual system.

Stores and adapters remain injectable, so the CLI, headless controller tests,
and native GUI share workflows without touching real accounts or repositories.
Widgets are never the durable source of truth.

## Persistence

```text
XDG config/codex-workbench/
    projects.json                      # project schema version 4
    projects.json.v0.3.1.bak           # legacy one-time backup
    projects.json.v0.4.0.bak           # schema-3 -> schema-4 backup
    settings.json                      # UI/integration preferences

XDG data/codex-workbench/
    activity.json                      # bounded explicit-action history
    sessions/
        <project-id>/
            current.json
            <session-id>/
                session.json
                handoff.md
                transcript.md
                handoffs/<handoff-id>/
```

Schema 4 adds a `workspace` object to each Project:

```text
workspace
  info_collapsed
  prompt_hold
  commands[]
  panes[]                 # IDs, provider type/title/config, dock metadata
  layout                  # recursive pane/split nodes and ratios
  focused_pane_id
```

Schema-3/v0.4 projects load with an expanded empty workspace and are written as
schema 4 on the next project save. Unknown document, Project, workspace,
command, pane, associated-path, and split-node fields survive round trips where
their model has an extension boundary. PTY handles, child processes, widgets,
WebViews, and callbacks are runtime-only.

## Project identity and ownership

A Project remains the durable Workbench identity, not an alias for one Git
repository or Work Session. Its canonical root is the default directory for
Codex, sessions, safety operations, and panes. Associated paths are labeled
secondary roots with an extensible role, shell permission, optional required
flag, and live Git metadata.

The Project owns persistent workspace configuration. Work Sessions continue to
own task/handoff state and may influence the effective Codex account/objective;
they do not own or replace the pane layout. `registry_id` keys both durable
workspace isolation and runtime isolation, so display-name changes do not leak
or discard panes.

## Project Information grid

Project Info uses a breakpoint-based `Gtk.Grid`; panels are packed into one,
two, or three columns with declared spans. The large layout deliberately forms
complete rows, and the medium layout gives Objective and Instructions full-row
spans. No panel uses arbitrary x/y coordinates. A per-project persisted flag
switches the grid to a thin state-derived summary rail.

The whole Project page is vertically scrollable. Provider surfaces keep their
own native scroll owners: VTE handles terminal scrollback/mouse mode, WebKit
handles page scrolling, and Files owns an inner `GtkScrolledWindow`. The outer
page adds no capture-phase wheel controller.

## Workspace and split layout

`SplitLayout` is either a pane leaf or a binary horizontal/vertical split.
`Gtk.Paned` renders each split, supplies draggable boundaries and minimum child
sizes, and persists a clamped ratio. Move commands remove one leaf and insert it
beside an anchor; there are no freeform coordinates.

Focus mode stores only `focused_pane_id` and renders that leaf without mutating
the split tree. Restoring Focus therefore returns to the exact prior tree.
Undocking removes the pane leaf while retaining an anchor/placement hint. The
same PaneRuntime/frame/provider surface moves to a Workbench-owned top-level
window. Closing that window docks safely; explicit Close Pane destroys the
runtime and removes its durable definition.

`WorkspaceRuntimeRegistry` is keyed first by stable project ID, then pane ID.
Switching projects detaches the visible tree but retains Terminal/Codex PTYs and
other provider surfaces. Switching back reparents the same frames. Editing a
Project closes only that project's runtimes before reconstruction, preserving
the v0.4 rule that changed roots cannot leave a stale visible cwd.

## Pane providers

The provider boundary exposes create/restore, state serialization, focus,
close, and dock/undock hooks. Initial providers are:

- Codex: `codex-start <account>` as argv in a VTE PTY at the configured project
  root. Project/session/account identity deduplicates ordinary CODEX clicks;
  explicit New Codex Pane creates another identity.
- Terminal: an interactive shell VTE at a canonical, associated, or explicitly
  validated in-project path. Project Commands feed text visibly into a matching
  live terminal or create one first.
- Browser: optional WebKitGTK 6 surface with Back, Forward, Reload, URL, and Open
  Externally. Missing GI or an unusable bubblewrap sandbox returns an unavailable
  surface/capability instead of breaking Workbench startup.
- Files: a lightweight directory list across canonical/associated roots with
  navigation, copy path, system open/reveal, and Shell Here.

## Terminal and Codex event/lifecycle rules

VTE receives cwd and argv separately; Workbench never constructs a shell string
for `codex-start`. Embedded Codex children inherit the process environment plus
`CODEX_START_HOSTED=1`, preventing newer PATH launchers from opening a nested
GTK terminal host. VTE owns scrollback, pixel/fallback scrolling, mouse
autohide, right-click paste, terminal resizing, and PTY close. Pane switching
does not close a PTY. Explicit Close Pane and application shutdown close it.
An exited Codex/Terminal surface is detectable; CODEX can restart the same pane
identity rather than creating a duplicate.

Workbench consumes the PATH launcher first and the bundled
`integrations/codex-launcher` fallback through the pre-existing adapter. The
fallback intentionally includes the synchronized core plus its required
GTK-free theme/status model. The standalone GTK host, PTY bridge, and browser
theme editor remain out of this bundle until a shared status-rail integration
can adopt those modules and their tests together.

## Platform and browser boundaries

Linux remains the supported runtime. The platform service continues to isolate
external terminal, CLI clipboard, chooser, URL/folder open, launcher, path, and
executable behavior. VTE and WebKit are GUI runtime capabilities and never
enter the core service import graph.

WebKitGTK uses child processes. Capability detection therefore checks both the
GI namespace and, when installed, a no-write bubblewrap user-namespace probe.
Workbench does not disable the WebKit sandbox to force a browser surface in a
restricted environment.

## Safety choices

- Discovered commands are suggestions only; they are never added or run
  automatically.
- Project Commands execute in visible VTE terminals, never hidden subprocesses.
- Pane paths must resolve to the canonical root, an associated root, or a child
  of those configured roots.
- Prompt Hold performs one asynchronous text snapshot and never monitors the
  clipboard or stores history.
- Effective Git identity is detected; duplication is optional.
- Expected identity, remote URL, and owner are checked only when configured.
- Clipboard failures cannot abort context generation.
- Commit staging requires `--file`, `--all`, or pre-existing staged changes.
- Push destination and identity checks render before network I/O.
- Resume never changes branches or working files automatically.
- Clone registration remains transactional; removal preserves source, Git,
  sessions, handoffs, and history.
- GUI startup performs read-only integration checks and reconstructs only the
  selected project's provider surfaces.
- Account-strip selection changes intent only; it never launches or hands off.
