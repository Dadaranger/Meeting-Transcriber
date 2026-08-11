# Development workflow

## History policy

The repository should preserve meaningful implementation history. Development is
performed in small, reviewable increments instead of one large final commit.

For each increment:

1. Define one observable outcome and its acceptance check.
2. Implement only the files required for that outcome.
3. Run the relevant focused checks.
4. Review `git status` and the staged diff explicitly.
5. Commit with a terse description of the outcome.
6. Push the commit immediately to the active remote branch.
7. Keep the draft pull request description and checklist current.

Examples of appropriately scoped commits:

- `document product and architecture plan`
- `scaffold desktop application shell`
- `persist meeting session state`
- `enumerate Windows audio devices`
- `record microphone audio chunks`
- `capture WASAPI loopback audio`
- `transcribe a recorded session offline`
- `export timestamped meeting Markdown`

Avoid mixing refactors, dependency upgrades, generated files, and user-facing
features in the same commit unless they are inseparable.

## Branching

- `main` contains stable, understandable checkpoints.
- Implementation work uses a `codex/<short-description>` branch.
- A draft pull request is opened early for each milestone-sized branch.
- The active branch is pushed after every validated commit.
- History is not force-pushed unless the repository owner explicitly requests it.

The first commit is a special case because the remote repository has no commits
and therefore no base branch for a pull request. It establishes `main` with the
planning foundation. Subsequent increments follow the branch and draft-PR flow.

## Validation expectations

Validation grows with the project:

- Documentation-only changes: link/path inspection and whitespace checks
- Domain/storage changes: unit tests and schema/migration tests
- Capture changes: unit tests plus recorded-device smoke tests
- Processing changes: unit tests plus deterministic audio fixtures
- UI changes: application smoke test and targeted interaction tests
- Packaging changes: install/launch/uninstall on a clean Windows environment

If a check cannot run, the commit/PR notes must say why and identify the risk.

## Generated and sensitive data

Never commit:

- Meeting audio, transcripts, or exports containing real conversations
- Model weights or model caches
- API tokens, credentials, or local configuration containing secrets
- Build output, installer output, logs, or crash dumps
- Unlicensed test media

Test fixtures must be synthetic, explicitly licensed, or recorded for this
purpose with consent.
