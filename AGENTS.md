# Repository guidance

- Do not use regex in code; use string methods or conditionals.
- This repo is public on github, when open pr, upload release or artifacts, make sure no secrets or sensitive info was there.
- When PR generated and merged, if agent used openspec for changes and that feature was completed, archive them in openspec document with a new pr and auto merge the archive change branch.
- Usually after a feature or fix is done, auto merge.
- if deploy branch is set to hotfix branch, after merge to main port the commits to hotfix branch and production will auto cicd into server.

## Worker worktree layout

- Put worker worktrees under the primary repository root at `PROJECT_ROOT/.worktrees/<worker-name>`; keep `/.worktrees/` ignored. Do not use a global worktree directory for new workers.
- Before creating or reusing a worker, verify its repository, branch, HEAD, dirty state and owning worker/thread. Coordinate active workers before changing a checkout path.
- Relocate existing worker checkouts with `git worktree move`, preserving their branches, HEADs, tracked/untracked changes and ignored local files. Verify Git state before and after each move, and resume workers with the new explicit paths.
- Saved T3/provider paths may remain stale when the user explicitly authorizes direct Git relocation. A stale saved binding alone is not a blocker in that case. Do not edit internal application databases or add compatibility symlinks.
- Coordinate actual active processes before moving. Report migration counts, new paths, stale saved bindings and any genuine exceptions; report missing/prunable paths as missing rather than recreating contents.
