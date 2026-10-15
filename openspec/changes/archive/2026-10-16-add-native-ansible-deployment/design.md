# Design

The role and public host helpers are released with the application. CI retains
existing optimized image rebuild/retag behavior and captures each actual digest.
A manifest job waits for the required application/deployment checks before
recording the exact source, run, attempt and both images. A successful main
workflow event dispatches the shared manual deployment workflow; both dispatch
and selection independently skip obsolete automatic revisions. Manual rollback
can select an older successful release with a retained manifest and image.

The controller exports only public deployment files from the selected Git
commit. The host resolves its existing configuration through Compose in memory;
credential content is not transported through Ansible or GitHub. MCP checks
execute inside the container, using its runtime environment without exporting
the bearer token. Diagnostic failures do not relay raw Compose errors or broker
response bodies.

Migration discovers only container labels and mounts and requires the operator's
explicit existing Compose project. OpenD state must be a present named volume;
all known journal mounts are retained as external references. The first version
refuses writable modes and bind-mounted state. It leaves existing source and
private configuration intact for first-migration rollback.

Native callers share a host lock. Each source/image/tunnel selection has its
own release path, and an existing release with different public settings cannot
be overwritten. A failed application or selected-tunnel check restores the
previous release; the first migration restores the legacy selection. OpenD
readiness remains a separate warning after authenticated MCP verification.
After successful application validation, the selected release becomes current
and its user service replaces legacy boot startup.

The synthetic fixture runs the real Ansible tasks against a disposable local
inventory with Docker command stubs; systemd activation is excluded there. Real
Compose separately renders the overlay. A local HTTP fixture checks MCP
authentication, request/response validation and read-only gateway probes. These
checks do not establish VPS, broker or hosted ChatGPT acceptance. Production
cutover must verify user-service startup and the real endpoint separately.
