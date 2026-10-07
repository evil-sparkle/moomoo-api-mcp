# Development workflow

Use Python 3.12 locally and preserve Python 3.10 compatibility. `pyproject.toml`
defines the supported syntax and type target; `uv.lock` pins dependencies.
Tests mock the SDK and do not need a live OpenD gateway.
On macOS, the CI script tests need Bash 4 or newer (`mapfile` is unavailable in
Apple's Bash 3.2). Install `bash` with Homebrew and put its `bin` directory first
on PATH for the test command. The tests execute `bash` from PATH.

## Checks and completion

Follow the [completion policy](../AGENTS.md#completion-policy).
Select the relevant checks below; this is a command reference, not a requirement
to run every command for every task.

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
| Documentation | Review acceptance criteria, links, and consistency |
| Specs or managed OpenSpec workflow instructions | Strict OpenSpec validation; for regeneration, confirm version, exact workflow inventory, and repeatability |

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

`openspec-verify-change` is an agent skill (`$openspec-verify-change` in Codex or
`/openspec-verify-change` in slash-invoked skill interfaces), not an
`openspec verify` CLI subcommand. It reviews completeness, correctness, and
coherence against change artifacts. CLI `validate` checks structure.

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
again to ensure no further changes. The [completion policy](../AGENTS.md#completion-policy)
lives outside generated files so regeneration preserves it.

Keep `openspec/config.yaml` limited to invariants and pointers. Add deeper
explanations to the appropriate indexed document. This uses the same on-demand
loading principle described in [OpenAI's skill documentation](https://developers.openai.com/codex/skills/).
