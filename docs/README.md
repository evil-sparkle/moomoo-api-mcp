# Documentation index

Read the entry relevant to the current task rather than loading every runbook.
Normative requirements live in [OpenSpec specs](../openspec/specs/); the files below
explain implementation and operations. Resolve disagreements against the specs
and current code, and update the stale documentation.

| Task | Reference |
| --- | --- |
| Implement, test, verify, or regenerate OpenSpec workflows | [Development workflow](development.md) |
| Understand runtime constraints, modules, SDK conventions, or architectural contracts | [Project guide](project-guide.md) |
| Deploy or upgrade the brokerage container | [VPS deployment](deploy-vps.md) |
| Operate Docker without a root daemon | [Rootless Docker](rootless-docker.md) |
| Diagnose process recovery, connection ownership, or persisted state | [State and restarts](state-and-restarts.md) |
| Operate paper execution, backups, or recovery review | [Paper execution](paper-execution.md) |
| Deploy or diagnose the optional official ChatGPT tunnel | [Private ChatGPT access](private-chatgpt-mcp.md) |

Agentic GitHub maintenance is deferred to a separate change. Its initial scope
will be CI diagnostics, documentation synchronization, issue triage, and reports
with restricted permissions and safe outputs. Brokerage feature development,
trading actions, and deployment automation are outside that initial scope.
