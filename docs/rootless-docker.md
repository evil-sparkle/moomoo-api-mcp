# Rootless Docker on macOS

Docker Engine needs Linux. On this Apple Silicon Mac, Lima runs an ARM Linux
VM with a rootless Docker daemon; Rosetta executes the x86_64 image binaries.
Both Compose services target `linux/amd64`, matching the remote x86_64 server.
The VM itself remains ARM64, so this is not a native x86_64 runtime test.

## Initial setup

Install the host tools:

```sh
brew install lima docker docker-compose docker-buildx
```

Add `/opt/homebrew/lib/docker/cli-plugins` to `cliPluginsExtraDirs` in
`~/.docker/config.json`, preserving any existing settings.

Create the VM and Docker context:

```sh
limactl start --name=moomoo-rootless --vm-type=vz --rosetta \
  --cpus=4 --memory=6 --disk=40 --mount-none -y template:docker
docker context create lima-moomoo-rootless \
  --docker "host=unix://$HOME/.lima/moomoo-rootless/sock/docker.sock"
docker context use lima-moomoo-rootless
```

No host directories are mounted into the VM. Docker sends the build context
through its socket; Compose's default named volume stores OpenD state inside
the VM. A macOS path in `OPEND_DATA_DIR` would require a separately configured
VM mount. Avoid deleting the VM if it holds session data you need.

## Everyday use

```sh
limactl start moomoo-rootless
docker context use lima-moomoo-rootless
docker info --format '{{json .SecurityOptions}}'
# Must include name=rootless.
docker compose build
```

To release VM resources:

```sh
limactl stop moomoo-rootless
```

Building does not start the application or log into Moomoo. Start the stack
separately once its runtime configuration is ready.

## Remote Linux deployment

The remote server needs its own rootless Docker installation. Check its
`docker info` output for `name=rootless` using the deployment user's context.
For CI-built ECR images, follow [the VPS deployment runbook](deploy-vps.md).
It pulls both images using the rootless context; no build on the VPS is needed.
Local images are not automatically copied to the remote server.

Rootless mode controls the daemon's host privileges. UID 0 inside a container
is mapped into an unprivileged host user's namespace; `/root` inside the OpenD
container does not mean the Docker daemon runs as host root.

References: [Lima Docker template](https://github.com/lima-vm/lima/blob/master/templates/docker.yaml),
[Lima architecture emulation](https://lima-vm.io/docs/config/multi-arch/),
[Docker rootless mode](https://docs.docker.com/engine/security/rootless/).
