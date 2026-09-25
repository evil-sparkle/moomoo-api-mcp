"""Actual pinned-client checks with synthetic credentials and disposable resources.

The control-plane simulation is isolated from internet egress. This is not live
OpenAI acceptance, and it never clears the separate credential-confinement gate.
"""

import argparse
import atexit
import errno
import hashlib
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import yaml

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--docker-context", default="rootless")
parser.add_argument("--broker-image", required=True)
parser.add_argument("--tunnel-image", default="moomoo-chatgpt-tunnel:development")
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[2]
busy_port = socket.socket()
try:
    busy_port.bind(("127.0.0.1", 8000))
except OSError as exc:
    busy_port.close()
    if exc.errno != errno.EADDRINUSE:
        raise
    print("Port 8000 already occupied; existing listener left untouched", flush=True)
else:
    busy_port.listen()
    atexit.register(busy_port.close)
    print("Disposable dummy occupies port 8000 throughout this fixture", flush=True)
workspace = pathlib.Path(tempfile.mkdtemp(prefix="tunnel-runtime-fixture-"))
atexit.register(shutil.rmtree, workspace, ignore_errors=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
workspace.chmod(0o755)
shutil.copytree(
    root / "src", workspace / "src", ignore=shutil.ignore_patterns("__pycache__")
)
for path in (workspace / "src").rglob("*"):
    path.chmod(0o755 if path.is_dir() else 0o644)
(workspace / "src").chmod(0o755)
shutil.copyfile(root / "tests/fixtures/opend_stub.py", workspace / "opend_stub.py")
(workspace / "opend_stub.py").chmod(0o755)
docker = ["docker", "--context", args.docker_context]
project = "tunnel-fixture-" + uuid.uuid4().hex[:12]


def run(args, **kw):
    result = subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=90, **kw
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


image = run(docker + ["image", "inspect", "--format", "{{.Id}}", args.tunnel_image])
# Isolate real child-exit/reaping behavior from startup checks already exercised
# below. Only this no-network subtest bypasses the gate and deliberately gives
# the unmodified binary a missing config, forcing a real process exit.
exit_probe = """import sys
sys.path.insert(0, '/opt/tunnel')
import runtime
runtime.verify_config=lambda: None
runtime.child_environment=lambda: {'PATH':'/usr/local/bin:/usr/bin:/bin'}
runtime.gate=lambda stop: True
runtime.CONFIG='/missing-synthetic-config'
assert runtime.main()==1
print('PASS: real client exit is reaped and manager returns nonzero')
"""
print(
    run(
        docker
        + [
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10002:10002",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "python",
            image,
            "-c",
            exit_probe,
        ]
    ),
    flush=True,
)
docker_host = run(
    docker
    + [
        "context",
        "inspect",
        args.docker_context,
        "--format",
        "{{.Endpoints.docker.Host}}",
    ]
)
mapping = json.loads(
    run(
        [
            "sudo",
            "-n",
            "python3",
            str(root / "scripts/stage_tunnel_secrets.py"),
            "--docker-host",
            docker_host,
            "--image",
            image,
        ]
    )
)
# Public config contains file references; protected fixture masters are root-only.
config = (
    (root / "deploy/tunnel-client/container/tunnel-client.yaml")
    .read_text()
    .replace("https://api.openai.com", "http://tunnel-control-plane:8081")
)
(workspace / "client.yaml").write_text(config)
(workspace / "client.yaml").chmod(0o644)
(workspace / "config.sha256").write_text(hashlib.sha256(config.encode()).hexdigest())
(workspace / "config.sha256").chmod(0o644)
secret_fixture = str(root / "tests/fixtures/tunnel_secret_fixture.py")
secret_root = pathlib.Path(
    json.loads(
        run(
            [
                "sudo",
                "-n",
                "python3",
                secret_fixture,
                "create",
                "--daemon-uid",
                str(mapping["daemon_uid"]),
                "--runtime-uid",
                str(mapping["uid"]),
            ]
        )
    )["directory"]
)


def cleanup_secrets():
    if secret_root.exists():
        run(
            [
                "sudo",
                "-n",
                "python3",
                secret_fixture,
                "remove",
                "--directory",
                str(secret_root),
            ]
        )


atexit.register(cleanup_secrets)
staging_command = [
    "sudo",
    "-n",
    "python3",
    str(root / "scripts/stage_tunnel_secrets.py"),
    "--docker-host",
    docker_host,
    "--image",
    image,
    "--master-directory",
    str(secret_root / "master"),
    "--staging-directory",
    str(secret_root / "staged"),
]
run(staging_command)
print(
    run(
        [
            "sudo",
            "-n",
            "python3",
            secret_fixture,
            "verify-rejections",
            "--directory",
            str(secret_root),
            "--runtime-uid",
            str(mapping["uid"]),
            "--runtime-gid",
            str(mapping["gid"]),
        ]
    ),
    flush=True,
)
run(staging_command)
fixture = {
    "services": {
        "moomoo-mcp": {
            "image": args.broker_image,
            "build": None,
            "container_name": None,
            "environment": {
                "MOOMOO_LOGIN_ACCOUNT": "synthetic",
                "MOOMOO_LOGIN_PWD_MD5": "synthetic",
                "MCP_AUTH_TOKEN": "synthetic-mcp-token",
                "MCP_ALLOW_CHATGPT_TUNNEL_HOST": "1",
                "PYTHONPATH": "/fixture-src",
            },
            "networks": ["default", "fixture-only"],
            "volumes": [
                str(workspace / "opend_stub.py") + ":/opt/moomooOpenD/OpenD:ro",
                str(workspace / "src") + ":/fixture-src:ro",
            ],
        },
        "chatgpt-tunnel": {
            "restart": "no",
            "networks": ["fixture-only"],
            "volumes": [
                str(workspace / "client.yaml") + ":/etc/tunnel-client.yaml:ro",
                str(workspace / "config.sha256") + ":/opt/tunnel/config.sha256:ro",
            ],
        },
        "tunnel-control-plane": {
            "image": image,
            "entrypoint": ["python", "/fixture.py"],
            "user": "10002:10002",
            "read_only": True,
            "networks": ["fixture-only"],
            "volumes": [
                str(root / "tests/fixtures/tunnel_control_plane.py")
                + ":/fixture.py:ro",
                str(secret_root / "staged") + ":/expected:ro",
            ],
        },
    },
    "networks": {"default": {"internal": False}, "fixture-only": {"internal": True}},
}
# YAML override tags are required to replace the fixed production host publication.

text = yaml.safe_dump(fixture)
text = text.replace(
    "    container_name: null",
    '    container_name: !reset null\n    ports: !override ["127.0.0.1::8000"]',
).replace("    build: null", "    build: !reset null")
(workspace / "fixture.yml").write_text(text)
(workspace / "empty").write_text("")
env = {
    "PATH": os.environ["PATH"],
    "HOME": os.environ["HOME"],
    "CHATGPT_TUNNEL_IMAGE": image,
    "CHATGPT_TUNNEL_SECRET_DIR": str(secret_root / "staged"),
}
compose = docker + [
    "compose",
    "-p",
    project,
    "--env-file",
    str(workspace / "empty"),
    "-f",
    str(root / "docker-compose.yml"),
    "-f",
    str(root / "docker-compose.chatgpt.yml"),
    "-f",
    str(workspace / "fixture.yml"),
]


def control_stats():
    return json.loads(
        run(
            compose
            + [
                "exec",
                "-T",
                "tunnel-control-plane",
                "python",
                "-c",
                "import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8081/stats'))))",
            ],
            env=env,
        )
    )


def wait_for(predicate, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(2)
    raise AssertionError("bounded fixture condition not met")


try:
    run(
        compose + ["up", "-d", "--no-build", "tunnel-control-plane", "chatgpt-tunnel"],
        env=env,
    )
    time.sleep(6)
    assert control_stats()["authenticated"] == 0, "client polled before MCP gate"
    print("PASS: no official-client polling while MCP startup is delayed", flush=True)
    run(compose + ["up", "-d", "--no-build", "--no-deps", "moomoo-mcp"], env=env)
    wait_for(lambda: control_stats()["forwarded_ok"] == 3, 100)
    tunnel_id = run(compose + ["ps", "-q", "chatgpt-tunnel"], env=env)
    assert tunnel_id, "actual tunnel entrypoint did not remain running"
    ports = json.loads(
        run(
            docker
            + ["inspect", "--format", "{{json .NetworkSettings.Ports}}", tunnel_id]
        )
    )
    assert not ports, "tunnel unexpectedly publishes a port"
    stats = control_stats()
    print(json.dumps({"simulated_control_plane": stats}, sort_keys=True), flush=True)
    assert stats["authenticated"] > 0 and stats["forwarded_ok"] == 3, (
        "normal authenticated forwarding not proven"
    )
    # Exercise ordinary MCP bearer conflicts through the unmodified official binary.
    for route in ("enqueue-wrong", "enqueue-conflict"):
        observed = control_stats()
        enqueue_negative = (
            "import urllib.request;urllib.request.urlopen(urllib.request.Request("
            "'http://127.0.0.1:8081/" + route + "',data=b''))"
        )
        run(
            compose
            + ["exec", "-T", "tunnel-control-plane", "python", "-c", enqueue_negative],
            env=env,
        )
        wait_for(
            lambda observed=observed: control_stats()["responses"]
            > observed["responses"]
        )
        assert control_stats()["forwarded_ok"] == observed["forwarded_ok"], (
            route + " unexpectedly succeeded"
        )
        print("PASS: official forwarding rejects " + route, flush=True)
    http_negative_probe = """import http.client,json
from pathlib import Path
auth=Path('/run/secrets/mcp-authorization').read_text().strip()
wrong=('Authorization','Bearer synthetic-wrong')
body=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
cases=[('approved',[('Authorization',auth)],'moomoo-mcp:8000',None,200),
       ('host',[('Authorization',auth)],'unexpected:8000',None,421),
       ('origin',[('Authorization',auth)],'moomoo-mcp:8000','http://moomoo-mcp:8000',403),
       ('missing',[],'moomoo-mcp:8000',None,401),
       ('wrong',[wrong],'moomoo-mcp:8000',None,401),
       ('conflicting',[('Authorization',auth),wrong],'moomoo-mcp:8000',None,401)]
for label,headers,host,origin,expected in cases:
    c=http.client.HTTPConnection('moomoo-mcp',8000,timeout=15)
    c.putrequest('POST','/mcp',skip_host=True)
    base=[('Host',host),('Content-Type','application/json'),
          ('Accept','application/json, text/event-stream'),
          ('Content-Length',str(len(body)))]
    for name,value in base+headers:
        c.putheader(name,value)
    if origin: c.putheader('Origin',origin)
    c.endheaders(body.encode())
    response=c.getresponse()
    assert response.status==expected, label+' unexpected HTTP status'
    response.read();c.close()
print('PASS: actual MCP exact Host, Origin and missing/wrong/conflicting bearer checks')
"""
    print(
        run(
            compose
            + ["exec", "-T", "chatgpt-tunnel", "python", "-c", http_negative_probe],
            env=env,
        ),
        flush=True,
    )
    network_probe = """import socket
s=socket.create_connection(('moomoo-mcp',8000),3)
s.close()
s=socket.socket()
s.settimeout(3)
assert s.connect_ex(('moomoo-mcp',11111))!=0
print('MCP reachable; OpenD bridge access refused')"""
    print(
        run(
            compose + ["exec", "-T", "chatgpt-tunnel", "python", "-c", network_probe],
            env=env,
        ),
        flush=True,
    )
    # A refused bridge port is evidence only if the loopback listener is alive.
    run(
        compose
        + [
            "exec",
            "-T",
            "moomoo-mcp",
            "python",
            "-c",
            "import socket;socket.create_connection(('127.0.0.1',11111),3).close()",
        ],
        env=env,
    )
    run(
        compose
        + [
            "exec",
            "-T",
            "tunnel-control-plane",
            "python",
            "-c",
            "import socket;s=socket.socket();s.settimeout(3);"
            "assert s.connect_ex(('chatgpt-tunnel',8080))!=0",
        ],
        env=env,
    )
    namespaces = (
        "import os,json;print(json.dumps([os.readlink('/proc/self/ns/'+n) "
        "for n in ('net','pid')]))"
    )
    brokerage_ns = json.loads(
        run(compose + ["exec", "-T", "moomoo-mcp", "python", "-c", namespaces], env=env)
    )
    tunnel_ns = json.loads(
        run(
            compose + ["exec", "-T", "chatgpt-tunnel", "python", "-c", namespaces],
            env=env,
        )
    )
    assert all(
        left != right for left, right in zip(brokerage_ns, tunnel_ns, strict=True)
    )
    print(
        "PASS: OpenD is alive on brokerage loopback; "
        "health/admin is private; namespaces differ",
        flush=True,
    )
    published = run(compose + ["port", "moomoo-mcp", "8000"], env=env)
    assert published.startswith("127.0.0.1:") and not published.endswith(":0"), (
        "missing ephemeral loopback publication"
    )
    print(
        "PASS: actual entrypoint, non-root runtime, Docker DNS, "
        "no tunnel ports, OpenD isolation",
        flush=True,
    )
    print(run(compose + ["logs", "--no-color", "chatgpt-tunnel"], env=env), flush=True)

    # Read-only mounts and the real numeric runtime identity, without echoing files.
    permissions = """import os
from pathlib import Path
assert os.getuid()==10002 and os.getgid()==10002
for name in ('control-plane-api-key','mcp-authorization','tunnel-id'):
    path=Path('/run/secrets')/name
    assert path.read_bytes()
    try: path.write_text('must-not-write')
    except OSError: pass
    else: raise AssertionError('writable credential mount')
try: Path('/tmp/must-not-write').touch()
except OSError: pass
else: raise AssertionError('writable root filesystem')
assert not Path('/opt/moomooOpenD').exists()
print('PASS: numeric UID reads intended mounts; writes refused')"""
    print(
        run(
            compose + ["exec", "-T", "chatgpt-tunnel", "python", "-c", permissions],
            env=env,
        ),
        flush=True,
    )
    for uid, gid in (
        (mapping["daemon_uid"], mapping["daemon_uid"]),
        (mapping["uid"] - 1, mapping["gid"] - 1),
        (mapping["uid"], mapping["gid"]),
    ):
        if uid:
            for area in ("master", "staged"):
                probe_code = (
                    "import os,sys; assert os.geteuid()==int(sys.argv[1]); "
                    "assert os.access(sys.argv[2],os.R_OK)==(sys.argv[3]=='1')"
                )
                run(
                    [
                        "sudo",
                        "-n",
                        "setpriv",
                        f"--reuid={uid}",
                        f"--regid={gid}",
                        "--clear-groups",
                        "python3",
                        "-c",
                        probe_code,
                        str(uid),
                        str(secret_root / area / "control-plane-api-key"),
                        "1" if uid == mapping["uid"] and area == "staged" else "0",
                    ]
                )
    print("PASS: unrelated identities cannot read protected host sources", flush=True)

    # Root master -> atomic mapped staging -> actual client authentication.
    before = control_stats()
    run(
        [
            "sudo",
            "-n",
            "python3",
            secret_fixture,
            "rotate-runtime",
            "--directory",
            str(secret_root),
        ]
    )
    run(staging_command)
    wait_for(lambda: control_stats()["rejected"] > before["rejected"], 30)
    print("PASS: old runtime key rejected after atomic staging", flush=True)
    old_mount = (
        "from pathlib import Path;"
        "assert Path('/run/secrets/control-plane-api-key').read_text()"
        "=='synthetic-runtime-key'"
    )
    run(compose + ["exec", "-T", "chatgpt-tunnel", "python", "-c", old_mount], env=env)
    print(
        "PASS: atomic replacement leaves the running file bind on its old inode",
        flush=True,
    )
    accepted_after_staging = control_stats()["authenticated"]
    run(compose + ["restart", "chatgpt-tunnel"], env=env)
    # Docker versions may remount file sources on restart. Record, do not assume.
    time.sleep(15)
    restarted = control_stats()["authenticated"] > accepted_after_staging
    print(json.dumps({"restart_observed_new_runtime_key": restarted}), flush=True)
    before_recreate = control_stats()["authenticated"]
    run(
        compose + ["up", "-d", "--no-deps", "--force-recreate", "chatgpt-tunnel"],
        env=env,
    )
    wait_for(lambda: control_stats()["authenticated"] > before_recreate)
    print(
        "PASS: recreated real client authenticates with rotated runtime key", flush=True
    )

    # The host client uses the ordinary MCP bearer, never the OpenAI runtime key.
    sys.path.insert(0, str(root))
    from scripts.private_chatgpt_preflight import McpClient, PreflightError

    def local_mcp(token="synthetic-mcp-token"):
        client = McpClient(
            "http://" + published + "/mcp", "Bearer " + token, timeout=10
        )
        client.initialize()
        client.list_tools()

    local_mcp()
    broker_id = run(compose + ["ps", "-q", "moomoo-mcp"], env=env)
    tunnel_id = run(compose + ["ps", "-q", "chatgpt-tunnel"], env=env)
    run(compose + ["stop", "chatgpt-tunnel"], env=env)
    local_mcp()
    assert (
        run(docker + ["inspect", "--format", "{{.State.ExitCode}}", tunnel_id]) == "0"
    )
    run(
        compose + ["up", "-d", "--no-deps", "--force-recreate", "chatgpt-tunnel"],
        env=env,
    )
    local_mcp()
    assert run(compose + ["ps", "-q", "moomoo-mcp"], env=env) == broker_id
    print("PASS: tunnel stop/recreate preserves authenticated host MCP", flush=True)

    # Record disposable persistent markers before MCP recreation.
    marker_code = (
        "from pathlib import Path; "
        "p=Path('/home/opend/.com.moomoo.OpenD/tunnel-fixture-marker');"
        "p.write_text('synthetic-state')"
    )
    run(compose + ["exec", "-T", "moomoo-mcp", "python", "-c", marker_code], env=env)
    networks = json.loads(
        run(
            docker
            + ["inspect", "--format", "{{json .NetworkSettings.Networks}}", broker_id]
        )
    )
    isolated = next(name for name in networks if name.endswith("_fixture-only"))
    old_ip = networks[isolated]["IPAddress"]
    run(compose + ["stop", "chatgpt-tunnel"], env=env)
    run(
        [
            "sudo",
            "-n",
            "python3",
            secret_fixture,
            "rotate-mcp",
            "--directory",
            str(secret_root),
        ]
    )
    run(staging_command)
    fixture["services"]["moomoo-mcp"]["environment"]["MCP_AUTH_TOKEN"] = (
        "synthetic-mcp-token-rotated"
    )
    updated = (
        yaml.safe_dump(fixture)
        .replace(
            "    container_name: null",
            '    container_name: !reset null\n    ports: !override ["127.0.0.1::8000"]',
        )
        .replace("    build: null", "    build: !reset null")
    )
    (workspace / "fixture.yml").write_text(updated)
    run(compose + ["stop", "moomoo-mcp"], env=env)
    run(compose + ["rm", "-f", "moomoo-mcp"], env=env)
    holder = project + "-old-ip"
    run(
        docker
        + [
            "run",
            "-d",
            "--name",
            holder,
            "--network",
            isolated,
            "--ip",
            old_ip,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import time;time.sleep(300)",
        ]
    )
    try:
        run(compose + ["up", "-d", "--no-deps", "moomoo-mcp"], env=env)
        replacement = run(compose + ["ps", "-q", "moomoo-mcp"], env=env)
        new_networks = json.loads(
            run(
                docker
                + [
                    "inspect",
                    "--format",
                    "{{json .NetworkSettings.Networks}}",
                    replacement,
                ]
            )
        )
        assert new_networks[isolated]["IPAddress"] != old_ip
        published = run(compose + ["port", "moomoo-mcp", "8000"], env=env)

        def new_local_ready():
            try:
                local_mcp("synthetic-mcp-token-rotated")
            except PreflightError:
                return False
            return True

        wait_for(new_local_ready)
        try:
            local_mcp()
        except PreflightError as exc:
            assert "HTTP 401" in str(exc)
        else:
            raise AssertionError("old ordinary MCP bearer still accepted")
        marker_check = (
            "from pathlib import Path;"
            "assert Path('/home/opend/.com.moomoo.OpenD/tunnel-fixture-marker')"
            ".read_text()=='synthetic-state'"
        )
        run(
            compose + ["exec", "-T", "moomoo-mcp", "python", "-c", marker_check],
            env=env,
        )
        before = control_stats()
        run(
            compose + ["up", "-d", "--no-deps", "--force-recreate", "chatgpt-tunnel"],
            env=env,
        )
        wait_for(lambda: control_stats()["authenticated"] > before["authenticated"])
        enqueue = "import urllib.request;urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8081/enqueue',data=b''))"
        run(
            compose + ["exec", "-T", "tunnel-control-plane", "python", "-c", enqueue],
            env=env,
        )
        wait_for(lambda: control_stats()["forwarded_ok"] > before["forwarded_ok"])
        print(
            "PASS: changed MCP IP resolves through DNS; new MCP bearer forwards; "
            "old bearer refused; state retained",
            flush=True,
        )
    finally:
        run(docker + ["rm", "-f", holder])

    # The real pinned child, not a stand-in, is stopped to simulate a stuck process.
    tunnel_id = run(compose + ["ps", "-q", "chatgpt-tunnel"], env=env)
    run(docker + ["update", "--restart", "unless-stopped", tunnel_id])
    before_restarts = int(
        run(docker + ["inspect", "--format", "{{.RestartCount}}", tunnel_id])
    )
    stop_client = (
        "import os,signal;from pathlib import Path;"
        "os.kill(int(Path('/run/moomoo-chatgpt-tunnel/client.pid').read_text()),"
        "signal.SIGSTOP)"
    )
    run(
        compose + ["exec", "-T", "chatgpt-tunnel", "python", "-c", stop_client], env=env
    )

    def independently_restarted():
        local_mcp("synthetic-mcp-token-rotated")
        return (
            int(run(docker + ["inspect", "--format", "{{.RestartCount}}", tunnel_id]))
            > before_restarts
        )

    wait_for(independently_restarted, 100)
    print(
        "PASS: hung real client triggers independent Docker restart; "
        "host MCP stays usable",
        flush=True,
    )

    # Repeat MCP IP replacement while keeping the tunnel process/container alive.
    broker_id = run(compose + ["ps", "-q", "moomoo-mcp"], env=env)
    networks = json.loads(
        run(
            docker
            + ["inspect", "--format", "{{json .NetworkSettings.Networks}}", broker_id]
        )
    )
    old_ip = networks[isolated]["IPAddress"]
    tunnel_id = run(compose + ["ps", "-q", "chatgpt-tunnel"], env=env)
    tunnel_image = run(docker + ["inspect", "--format", "{{.Image}}", tunnel_id])
    run(compose + ["stop", "moomoo-mcp"], env=env)
    run(compose + ["rm", "-f", "moomoo-mcp"], env=env)
    holder = project + "-dns-old-ip"
    run(
        docker
        + [
            "run",
            "-d",
            "--name",
            holder,
            "--network",
            isolated,
            "--ip",
            old_ip,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import time;time.sleep(300)",
        ]
    )
    try:
        run(compose + ["up", "-d", "--no-deps", "moomoo-mcp"], env=env)
        published = run(compose + ["port", "moomoo-mcp", "8000"], env=env)
        wait_for(new_local_ready)
        before = control_stats()["forwarded_ok"]
        run(
            compose + ["exec", "-T", "tunnel-control-plane", "python", "-c", enqueue],
            env=env,
        )
        wait_for(lambda: control_stats()["forwarded_ok"] > before)
        assert run(compose + ["ps", "-q", "chatgpt-tunnel"], env=env) == tunnel_id
        print(
            "PASS: running official client recovers after MCP IP replacement "
            "without tunnel recreation",
            flush=True,
        )
    finally:
        run(docker + ["rm", "-f", holder])

    # Control-plane availability is not the same as the client's local /readyz.
    control_id = run(compose + ["ps", "-q", "tunnel-control-plane"], env=env)
    restart_count = run(
        docker + ["inspect", "--format", "{{.RestartCount}}", tunnel_id]
    )
    run(docker + ["network", "disconnect", isolated, control_id])
    try:
        for _ in range(4):
            local_mcp("synthetic-mcp-token-rotated")
            time.sleep(10)
        local_status = (
            "import sys,json;sys.path.insert(0,'/opt/tunnel');import runtime;"
            "print(json.dumps({'liveness':runtime.probe('healthz'),"
            "'client_startup_readiness':runtime.probe('readyz')}))"
        )
        status = json.loads(
            run(
                compose
                + ["exec", "-T", "chatgpt-tunnel", "python", "-c", local_status],
                env=env,
            )
        )
        assert status["liveness"]
        assert (
            run(docker + ["inspect", "--format", "{{.RestartCount}}", tunnel_id])
            == restart_count
        )
        print(
            json.dumps({"control_plane_disconnected_local_status": status}), flush=True
        )
    finally:
        run(
            docker
            + [
                "network",
                "connect",
                "--alias",
                "tunnel-control-plane",
                isolated,
                control_id,
            ]
        )
    before = control_stats()["forwarded_ok"]
    run(
        compose + ["exec", "-T", "tunnel-control-plane", "python", "-c", enqueue],
        env=env,
    )
    wait_for(lambda: control_stats()["forwarded_ok"] > before)
    print(
        "PASS: control-plane outage avoids restart loop; "
        "authenticated forwarding recovers",
        flush=True,
    )

    # Test SIGINT on the actual manager after the child is running.
    run(docker + ["update", "--restart", "no", tunnel_id])
    run(docker + ["kill", "--signal", "SIGINT", tunnel_id])
    wait_for(
        lambda: run(docker + ["inspect", "--format", "{{.State.Running}}", tunnel_id])
        == "false",
        20,
    )
    assert (
        run(docker + ["inspect", "--format", "{{.State.ExitCode}}", tunnel_id]) == "0"
    )
    local_mcp("synthetic-mcp-token-rotated")
    assert (
        run(docker + ["inspect", "--format", "{{.Image}}", tunnel_id]) == tunnel_image
    )
    print(
        "PASS: real manager handles SIGINT; recorded image identity "
        "and host access preserved",
        flush=True,
    )

    # Exercise the actual manager against mode/auth/result refusals. These modes
    # exist only in a stdlib response fixture; brokerage trading is never enabled.
    shutil.copyfile(
        root / "tests/fixtures/tunnel_preflight_server.py",
        workspace / "preflight-server.py",
    )
    (workspace / "preflight-server.py").chmod(0o444)
    before = control_stats()["authenticated"]
    for behavior, diagnostic in (
        ("SIMULATE", "READ_ONLY"),
        ("REAL", "READ_ONLY"),
        ("wrong-auth", "HTTP 401"),
        ("malformed", "malformed JSON"),
    ):
        fixture["services"]["moomoo-mcp"]["image"] = image
        fixture["services"]["moomoo-mcp"]["entrypoint"] = [
            "python",
            "/preflight-server.py",
        ]
        fixture["services"]["moomoo-mcp"]["environment"]["FIXTURE_BEHAVIOR"] = behavior
        fixture["services"]["moomoo-mcp"]["volumes"] = [
            str(workspace / "preflight-server.py") + ":/preflight-server.py:ro"
        ]
        updated = (
            yaml.safe_dump(fixture)
            .replace(
                "    container_name: null",
                "    container_name: !reset null\n"
                '    ports: !override ["127.0.0.1::8000"]',
            )
            .replace("    build: null", "    build: !reset null")
        )
        (workspace / "fixture.yml").write_text(updated)
        run(
            compose + ["up", "-d", "--no-deps", "--force-recreate", "moomoo-mcp"],
            env=env,
        )
        result = subprocess.run(
            compose + ["run", "--rm", "--no-deps", "chatgpt-tunnel"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1 and diagnostic in result.stdout
        assert control_stats()["authenticated"] == before
        print(
            "PASS: actual manager refuses " + behavior + " before client polling",
            flush=True,
        )
    run(compose + ["stop", "moomoo-mcp"], env=env)
    started = time.monotonic()
    result = subprocess.run(
        compose + ["run", "--rm", "--no-deps", "chatgpt-tunnel"],
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert result.returncode == 1 and "deadline exhausted" in result.stdout
    assert time.monotonic() - started < 105
    assert control_stats()["authenticated"] == before
    print(
        "PASS: actual image exhausts bounded startup deadline without client polling",
        flush=True,
    )

finally:
    run(compose + ["down", "-v", "--remove-orphans"], env=env)
    run(
        [
            "sudo",
            "-n",
            "python3",
            secret_fixture,
            "remove",
            "--directory",
            str(secret_root),
        ]
    )
    shutil.rmtree(workspace)
