FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS verified
WORKDIR /build
COPY install.py release.json ./
RUN python install.py --destination /out/tunnel-client

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
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
