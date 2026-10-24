ARG GO_VERSION=1.27.3
FROM golang:${GO_VERSION}-bookworm AS go-toolchain

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS verified
# Refresh Debian CA and OpenSSL security packages in both build and runtime
# stages; reject an outdated OpenSSL package left in the pinned Python base.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade ca-certificates libssl3 openssl \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419~deb12u2 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3)" ge 3.0.22-1~deb12u1 \
    && rm -rf /var/lib/apt/lists/*
COPY harden_ca.py /opt/security/harden_ca.py
RUN python /opt/security/harden_ca.py
WORKDIR /build
COPY --from=go-toolchain /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:$PATH" GOTOOLCHAIN=local
COPY rebuild.py source.json ./
RUN python rebuild.py fetch --destination /source \
    && python rebuild.py build --source /source --destination /out/tunnel-client

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
# Refresh Debian CA and OpenSSL security packages in both build and runtime
# stages; reject an outdated OpenSSL package left in the pinned Python base.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade ca-certificates libssl3 openssl \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419~deb12u2 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3)" ge 3.0.22-1~deb12u1 \
    && rm -rf /var/lib/apt/lists/*
COPY harden_ca.py /opt/security/harden_ca.py
RUN python /opt/security/harden_ca.py
COPY --from=verified /out/tunnel-client /usr/local/bin/tunnel-client
COPY --chmod=0444 release.json source.json /opt/tunnel/
COPY --chmod=0444 private_chatgpt_preflight.py runtime.py /opt/tunnel/
COPY --chmod=0444 tunnel-client.yaml /etc/tunnel-client.yaml
RUN python -c "import hashlib;from pathlib import Path;p=Path('/opt/tunnel/config.sha256');p.write_text(hashlib.sha256(Path('/etc/tunnel-client.yaml').read_bytes()).hexdigest());p.chmod(0o444)" \
    && chmod 0555 /opt/tunnel
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/run/moomoo-chatgpt-tunnel
USER 10002:10002
WORKDIR /run/moomoo-chatgpt-tunnel
ENTRYPOINT ["python", "/opt/tunnel/runtime.py"]
