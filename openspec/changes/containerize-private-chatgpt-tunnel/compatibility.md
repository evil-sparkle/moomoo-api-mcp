# Pinned-client compatibility gate — BLOCKED

Checked 2026-09-25 after approval of PR #37 at `96b82bed1fcebe32b1076dbce49a6a3012d57238`.
The implementation branch carries those approved artifacts unchanged except task
progress and appended verification evidence. No migration runtime is enabled.

## Integrity and source inspection (task 1.1 complete)

The repository installer downloaded the official v0.0.14 Linux amd64 archive,
verified its existing SHA-256 pin before extraction/execution, installed it only
under a disposable `/tmp` directory, and checked its reported version.

- Version: `0.0.14+0f870e50a973fa820d4c409000059e181e8d242b`.
- Archive SHA-256: `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`.
- Tagged source checkout: `0f870e50a973fa820d4c409000059e181e8d242b`.
- `pkg/runtimeconfig/file_config.go` supports `health.listen_addr`,
  `health.url_file`, `process.pid_file`, file-backed control-plane API keys,
  and `mcp.extra_headers`/`mcp.discovery_extra_headers`.
- `cmd/client/main.go` installs cancellation for SIGINT and SIGTERM. Both real
  tested binaries stopped on SIGTERM within five seconds without SIGKILL.
- Health URL/PID files were placed in the disposable workspace; HOME was also
  isolated. This does not prove compatibility with the future read-only image.
- The full client exposes health/readiness and its admin surface; production
  configuration already pins the health listener to loopback. Container health,
  writable-path restrictions and liveness recovery remain untested.
- `pkg/mcpclient/internal/static_headers.go` scopes injected MCP headers to the
  configured origin. `pkg/mcpclient/channel_transport_factory.go` adds a runtime
  same-origin redirect policy. `pkg/oauth/discovery.go` separately restricts
  discovery redirects. These are source findings, not a completed MCP binary
  forwarding/discovery compatibility matrix.

## Demonstrated blocker (task 1.2 incomplete)

The **real verified official binary** was launched against an ephemeral loopback
control-plane fixture and its built-in MCP fixture. A different loopback server
was the redirect sink. The control-plane fixture returned HTTP 302 to that sink.
No real credentials, brokerage data, OpenAI API request, or deployment were used.
The fixture generates a synthetic key, supplies it through a mode-0600 file,
scrubs the child environment, captures no raw output, and reports only booleans.

| Official binary | Changed port | Changed host and port | Gate exit |
| --- | --- | --- | --- |
| v0.0.14, approved pin | Synthetic runtime key reached sink | Synthetic runtime key reached sink | 1 (FAIL) |
| v0.0.15, investigation only | Synthetic runtime key reached sink | Synthetic runtime key reached sink | 1 (FAIL) |

In all four cases the intended fixture was contacted, the sink was contacted,
the child stayed running, and SIGTERM shutdown required no forced kill.

The source explains the result: `pkg/controlplane/internal/tunnel_service_client.go`
constructs HTTP clients without a `CheckRedirect` policy, and
`pkg/controlplane/internal/roundtripper.go` unconditionally sets the runtime-key
Authorization header on each round trip, including a redirected request. The
runtime config schema has no control-plane redirect-disable setting; Harpoon's
redirect options govern a different subsystem and do not fix this path.

This is a local HTTP reproduction of client redirect behavior, not a claim that
the actual OpenAI HTTPS endpoint redirects to an attacker or has been exploited.
It nevertheless fails the approved requirement that credentials cannot be sent
to an unapproved destination through redirects. The container's namespace,
non-root UID and read-only filesystem would not fix HTTP redirect handling.

## Minimal upgrade investigation

The next stable official release available when checked was
[v0.0.15](https://github.com/openai/tunnel-client/releases/tag/v0.0.15), source
`a390c168ff1b2d14e73a95991c186c6aba3ff5a0`. Its Linux amd64 archive was verified
against the official release asset digest
`8c836dc5d68d68b663d9a5c5b28ff9fa780d9f7a3fffb1c306880b8f32fab5f1`, using a
separate disposable manifest. The repository's production manifest was not
changed. The same real-binary gate failed, so **no upgrade is recommended**.
A fixed official release with reviewed integrity pins and passing gates is the
preferred prerequisite for resuming. No patched binary, custom proxy, widened
trust boundary or silent exception has been introduced.

Source references:

- [v0.0.14 control-plane client](https://github.com/openai/tunnel-client/blob/0f870e50a973fa820d4c409000059e181e8d242b/pkg/controlplane/internal/tunnel_service_client.go)
- [v0.0.14 header injection](https://github.com/openai/tunnel-client/blob/0f870e50a973fa820d4c409000059e181e8d242b/pkg/controlplane/internal/roundtripper.go)
- [v0.0.15 control-plane client](https://github.com/openai/tunnel-client/blob/a390c168ff1b2d14e73a95991c186c6aba3ff5a0/pkg/controlplane/internal/tunnel_service_client.go)

## Reproduction

Use a fresh temporary directory and the repository's reviewed installer:

```sh
compat_dir=$(mktemp -d)
python3 deploy/tunnel-client/install.py --destination "$compat_dir/tunnel-client"
python3 scripts/check_tunnel_client_compatibility.py --binary "$compat_dir/tunnel-client"
```

The last command intentionally exits 1 on the affected release. Exit 0 means
only these two redirect scenarios passed; it does not establish the full
compatibility matrix. Exit 2 means the fixture/client could not run conclusively.
The script uses dynamically allocated loopback ports, removes generated
credentials, shuts down only its own processes/servers, and never touches port
8000, real secret files, or an existing Docker stack. Remove the temporary binary
directory after inspection. Do not use real keys to reproduce this.

## Remaining work

Task 1.2 is blocked rather than complete. MCP discovery/initialize/forwarding,
subdomain redirects, poisoned proxies, full simulated control-plane protocol,
container image/permissions/rotation, deployment wrappers, CI and runbook changes
remain pending. Task 1.3 and all dependent implementation tasks remain unchecked.
VPS, live OpenAI, ChatGPT web and iPad acceptance remain PENDING. The draft PR is
an implementation work-in-progress with a concrete blocker, not a completed
migration or a merge recommendation.
