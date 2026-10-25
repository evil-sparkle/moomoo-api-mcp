FROM python:3.13.17-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530 AS base
# Refresh Debian CA and OpenSSL packages in the shared build/runtime base.
# Reject outdated libssl3 independently from the Go binary's certificate denylist.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade ca-certificates libssl3 openssl \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419~deb12u2 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3)" ge 3.0.22-1~deb12u1 \
    && rm -rf /var/lib/apt/lists/*
FROM base AS verified
WORKDIR /build
COPY install.py release.json ./
RUN python install.py --destination /out/tunnel-client

FROM base
COPY --from=verified /out/tunnel-client /usr/local/bin/tunnel-client
COPY --chmod=0444 release.json /opt/tunnel/release.json
COPY --chmod=0444 private_chatgpt_preflight.py runtime.py /opt/tunnel/
COPY --chmod=0444 tunnel-client.yaml /etc/tunnel-client.yaml
RUN python -c "import hashlib;from pathlib import Path;p=Path('/opt/tunnel/config.sha256');p.write_text(hashlib.sha256(Path('/etc/tunnel-client.yaml').read_bytes()).hexdigest());p.chmod(0o444)" \
    && chmod 0555 /opt/tunnel
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/run/moomoo-chatgpt-tunnel
USER 10002:10002
WORKDIR /run/moomoo-chatgpt-tunnel
ENTRYPOINT ["python", "/opt/tunnel/runtime.py"]
