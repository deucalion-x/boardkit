# Repository instructions

## Skill ownership

- `board` owns work-item behavior: creating cards and issues, moving an item
  through its lifecycle, review, acceptance, closure, and other decisions about
  an item's state.
- `board-admin` owns board shape and setup: statuses, fields, options, saved
  views, and configuration needed to build the board.
- Keep this boundary strict. Do not duplicate work-item policy in
  `board-admin`, and do not put board configuration in `board`.
- Edit the canonical templates under `src/boardkit/templates/skills/`. Never
  edit the generated copies under `plugin/skills/` directly.
- After changing a skill template, run `board install-skill`,
  `board install-skill --check`, and the focused skill tests.
- Skills reach agents per user (the plugin, or `install.sh`), never as copies
  committed in a repository, this one included.

## Board template acceptance

- `scripts/contract.py` must build its throwaway board by rendering the
  canonical `src/boardkit/templates/board-template.toml`; never duplicate the
  template's columns or views in the contract script.
- After changing the board template, run the focused tests and apply it to the
  retained live throwaway board when one is available. Verify GitHub read-back
  matches the template before treating the change as complete.
- Create a fresh throwaway only when testing new-board creation itself, GitHub's
  seeded defaults, or a change that additive apply cannot prove on the retained
  board. Use `--keep --no-issue` when it should remain available for visual
  inspection, and remove obsolete failed or superseded test projects.
