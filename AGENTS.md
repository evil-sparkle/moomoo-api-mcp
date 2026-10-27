# Repository guidance

- Do not use regex in code; use string methods or conditionals.
- This repo is public on github, when open pr, upload release or artifacts, make sure no secrets or sensitive info was there.
- When PR generated and merged, if agent used openspec for changes and that feature was completed, archive them in openspec document with a new pr and auto merge the archive change branch.
- Usually after a feature or fix is done, auto merge.
- if deploy branch is set to hotfix branch, after merge to main port the commits to hotfix branch and production will auto cicd into server.
