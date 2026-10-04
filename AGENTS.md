# Repository instructions

## Skill ownership

- `board` owns work-item behavior: creating cards and issues, moving an item
  through its lifecycle, review, acceptance, closure, and other decisions about
  an item's state.
- `board-admin` owns board shape and setup: statuses, fields, options, saved
  views, and configuration needed to build the board.
- Keep this boundary strict. Do not duplicate work-item policy in
  `board-admin`, and do not put board configuration in `board`.
- Before using or changing either skill, run `board install-skill --check`.
  If it reports drift, run `board install-skill` and repeat the check before
  relying on the generated instructions.
- Edit the canonical templates under `src/boardkit/templates/skills/`. Never
  edit generated copies under `.claude/skills/` or `.agents/skills/` directly.
- After changing a skill template, run `board install-skill`,
  `board install-skill --check`, and the focused skill tests.
