# Development workflow

Use Python 3.12 locally and preserve Python 3.10 compatibility. `pyproject.toml`
defines the supported syntax and type target; `uv.lock` pins dependencies.
Tests mock the SDK and do not need a live OpenD gateway.
On macOS, the CI script tests need Bash 4 or newer (`mapfile` is unavailable in
Apple's Bash 3.2). Install `bash` with Homebrew and put its `bin` directory first
on PATH for the test command. The tests execute `bash` from PATH.

## Checks and completion

After apply, continue autonomously through the relevant checks. Inspect failures,
fix their causes, and repeat affected checks until passing. Do not weaken tests,
patch installed dependencies, or count skipped checks as passing to reach green.

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
npx -y @fission-ai/openspec@1.14.1 validate --all --strict --no-interactive
```

Select checks by scope:

| Change | Required evidence |
| --- | --- |
| Python behavior | Relevant tests during iteration, then full pytest, Ruff, and basedpyright |
| Shell, Compose, deployment, or CI YAML | Relevant script/topology tests and full pytest; lint/type checks for affected Python |
| Container behavior | Applicable `scripts/smoke-test.sh`, `scripts/test-paper-container.sh`, or `scripts/test-tunnel-container.sh` with Docker |
| Docs, specs, or managed workflow instructions | Review links and requirements; strict OpenSpec validation; for regeneration, confirm version, exact workflow inventory, and repeatability |

After tests pass, execute `openspec-verify-change` for the same change
(`$openspec-verify-change` in Codex or `/openspec-verify-change` where slash-invoked
skills are supported). Review completeness,
correctness, and coherence against its tasks, specs, design, code, and test
evidence. Fix actionable findings, rerun affected checks, and repeat verification.
Run strict validation as well. **Verify is an agent workflow, not an
`openspec verify` CLI subcommand.** `validate` checks structure, not implementation.
For maintenance without a change artifact, explicitly verify the requested
acceptance criteria and report that artifact-based verification was unavailable;
do not select an unrelated active change.

Return only when verified, genuinely blocked, or a decision requires human
judgment. Report checks and evidence, unresolved findings, and exact blockers.
Checked tasks do not prove completion. Keep any required live acceptance open:
stub-based container checks prove topology/recovery, not broker login, provider
response-loss handling, or ChatGPT/iPad acceptance. Do not archive incomplete work.

Keep verification reports in the PR description or comments and CI evidence;
do not add a `verification.md` artifact to each change.

Use Conventional Commits and merge through pull requests. Install local hooks:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

Ruff uses an 88-character line length. Tool docstrings are the descriptions agents
read, so include preconditions and failure modes as well as arguments.
The container smoke test substitutes `tests/fixtures/opend_stub.py` for OpenD.
The paper container checks cover persistence, process locking, backup/restore,
dirty rollback journals, and READ_ONLY without journal storage. Live provider
response-loss and external retry-chain acceptance remain separate evidence.

## OpenSpec version and managed integrations

Use **`@fission-ai/openspec@1.14.1`**, matching the exact pin in
`.github/workflows/ci.yml`. The unscoped npm package is unrelated. A machine's
bare `openspec` executable may be older; check `openspec --version` before use or
invoke the exact npm version above.

The repository profile in `openspec/profile.json` is **core + verify**:
`propose`, `explore`, `apply`, `update`, `sync`, `archive`, `verify`. Delivery is
skills only for the existing Codex, Antigravity, Claude, and OpenCode
integrations. Duplicate `/opsx:*` commands and Antigravity workflow files are not
installed. No other expanded workflows are installed.

Regenerate from the repository root with:

```bash
./scripts/update-openspec.sh
```

OpenSpec 1.14.1 stores workflow selection in global configuration. The script
supplies the tracked profile through a temporary `XDG_CONFIG_HOME`, runs the
exact pinned generator, and removes the temporary configuration. It does not
change other repositories' profiles. The script uses `init` with an explicit tool
list to refresh existing integrations: Antigravity and Codex share `.agents/skills`,
so `update` alone stops detecting Antigravity after its workflow files are removed.
Existing project context is preserved. Bare `openspec update` uses your personal
profile and can remove verify; use the script for this repository.

Review the generated diff, confirm skill metadata says `generatedBy: "1.14.1"`,
check that only the seven selected workflows exist, and regenerate
again to ensure no further changes. Keep repository-specific completion rules in
`AGENTS.md` and `openspec/config.yaml`, since generated files are overwritten.

Keep `openspec/config.yaml` limited to invariants and pointers. Add deeper
explanations to the appropriate indexed document. This uses the same on-demand
loading principle described in [OpenAI's skill documentation](https://developers.openai.com/codex/skills/).
