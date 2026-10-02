# Proposal validation record

Date: 2026-09-25. Planning only; no implementation or implementation PR.

- Repository baseline: `origin/main` at `cd6bb2d821550d82b90dfba0214e9891a3c319f5`, merged PR #36.
- Isolated branch: `proposal/containerize-private-chatgpt-tunnel`.
- Authoritative planning root, resolved by `openspec context --json` after creating the worktree: `/tmp/moomoo-containerize-private-chatgpt-tunnel`.
- CLI: installed `@fission-ai/openspec` 1.13.1, matching CI. The plain executable was absent from PATH; the existing cached executable was used without installing or upgrading it.
- `openspec validate containerize-private-chatgpt-tunnel --strict --no-interactive`: PASS.
- `openspec validate --all --strict --no-interactive`: PASS, **27 passed, 0 failed**. Informational notices concern long existing requirement text and the existing unrelated skip-specs change; no validation failures.
- Proposal, design, four capability deltas and tasks are present. Every previous scenario in each modified requirement is retained; additions distinguish default, containerized and legacy behavior.
- Only this new change directory is edited. Canonical specs, archived PR #36 artifacts and project code are unchanged. Original checkout and unrelated untracked backup remain untouched.

The design's CI/acceptance matrix is a future verification plan. No image build, Docker runtime, host secret provisioning, real credential inspection, VPS deployment, credentialed OpenAI request, ChatGPT invocation or iPad acceptance was performed. Those results must not be inferred from schema validation.

The proposal is ready for review. Implementation requires a new apply request.

## Apply attempt after proposal approval — 2026-09-25

The owner approved PR #37 at `96b82bed`. Its artifacts were carried onto isolated
implementation branch `feat/containerize-private-chatgpt-tunnel` from main.
Task 1.1 is complete; task 1.2 is blocked. See `compatibility.md` for source
findings, reproduction and limits. No production release pin was changed.

Actual results:

- Official v0.0.14 archive integrity/version verification: PASS.
- Official v0.0.15 candidate archive integrity/version verification: PASS.
- Real-binary redirect gate: FAIL for both versions, on both changed-port and
  changed-host-and-port scenarios (4 failing scenarios); synthetic runtime key
  reached each unapproved sink, with no real credentials or external API traffic.
- `uv run ruff check scripts/check_tunnel_client_compatibility.py`: PASS.
- Ruff formatting applied to the compatibility script.
- `uv run basedpyright scripts/check_tunnel_client_compatibility.py`: PASS,
  0 errors/warnings/notes.
- `uv run pytest -q tests/test_tunnel_deployment.py`: 9 passed, 1 existing
  protobuf deprecation warning.
- `openspec validate --all --strict --no-interactive`: 27 passed, 0 failed.

Container builds/runtime tests, migration code, CI integration, full test suite,
VPS deployment and all live acceptance remain unperformed. The separate PR is a
blocked draft, not a completed implementation. The approved design requires
stopping dependent work when the pinned client fails credential confinement;
v0.0.15 is not a viable fix based on the same failing test.
