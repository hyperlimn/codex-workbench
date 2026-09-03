# Bundled codex-start fallback

Codex Workbench prefers `codex-start` from `PATH` and uses this directory only
as its packaged fallback.

The bundle intentionally contains:

- `codex-start`, the executable wrapper;
- `codex_start.py`, the sanitized launcher core; and
- `codex_terminal_theme.py`, the toolkit-neutral theme/status model imported by
  the core.

The standalone launcher's optional GTK/VTE host, PTY bridge, and browser theme
editor are not runtime requirements for Workbench's embedded terminal path.
Workbench sets `CODEX_START_HOSTED=1` for its VTE child so both a PATH launcher
and this fallback use that existing terminal instead of opening a nested host.
When `codex_terminal_ui.py` is absent outside Workbench, the launcher core also
falls back to the current terminal. Do not partially copy those optional
layers: a future shared status-rail integration should bring over the host,
bridge, UI, and their focused tests as one reviewed change.

`codex_start.py` is kept byte-for-byte aligned with the sanitized public
launcher source. The public bundle contains no account definitions or account
to-theme mappings; account and theme configuration remains local XDG user data.
