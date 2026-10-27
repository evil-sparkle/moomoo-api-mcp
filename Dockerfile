# One image, two processes: the OpenD gateway and the MCP server, started and
# supervised by moomoo_mcp.supervisor as PID 1.
#
# The base is OpenD's, not Python's. OpenD ships as an Ubuntu 18.04 build with
# its own shared libraries and is the component with real opinions about what
# it runs on; the interpreter is the portable half, so uv brings its own rather
# than putting OpenD on a distribution nobody has tested it against.
# Pinned by digest so a rebuild of this commit starts from the same rootfs. The
# apt layer below is still resolved at build time, so this is reproducibility of
# the base, not of the whole image; bump with `docker buildx imagetools inspect
# ubuntu:22.04`.
ARG UBUNTU_DIGEST=sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02
FROM ubuntu:22.04@${UBUNTU_DIGEST}

# ca-certificates/curl/tar/gzip fetch OpenD; keep its libssl-dev dependency.
# Reject an older distro OpenSSL even when its CA package is current. This does
# not replace OpenD's separately bundled libraries or establish their patch status.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    gzip \
    libssl-dev \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20260816ubuntu0.22.04.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3)" ge 3.0.2-0ubuntu1.30 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# OpenD. These pins are the single source of truth for version/tag/URL/checksum
# and are carried over unchanged from the image this one replaces.
# ---------------------------------------------------------------------------
WORKDIR /opt/moomooOpenD

# They can be overridden with docker compose build --build-arg NAME=value.
ARG OPEND_VERSION=10.10.7008
ARG OPEND_TAG=v10.10.7008-opend
ARG OPEND_URL=https://github.com/evil-sparkle/moomoo-api-mcp/releases/download/${OPEND_TAG}/moomoo_OpenD_${OPEND_VERSION}_Ubuntu18.04.tar.gz
# Must match the GitHub Release asset, which is what this build actually fetches.
# Read it from the API rather than hashing a local copy -- a partial download
# hashes cleanly and pins a corrupt artifact:
#   gh api repos/evil-sparkle/moomoo-api-mcp/releases/tags/${OPEND_TAG} \
#     --jq '.assets[] | select(.name | startswith("moomoo_OpenD_")) | .digest | ltrimstr("sha256:")'
ARG OPEND_SHA256=72eaa6e47b5cb8905306427b5e3679d591408243492e3e7acbc3a7d46f09a0aa

# Download OpenD from GitHub Release (with fallback to Moomoo CDN) and verify SHA256 integrity
RUN echo "Fetching OpenD from ${OPEND_URL}..." \
    && mkdir -p /tmp/opend_unpack \
    && if ! curl -fL --retry 5 --retry-delay 3 --retry-all-errors -C - --connect-timeout 20 "${OPEND_URL}" -o /tmp/opend.tar.gz; then \
         echo "GitHub Release asset not reachable, falling back to Moomoo CDN..."; \
         rm -f /tmp/opend.tar.gz; \
         curl -fL --retry 5 --retry-delay 3 --retry-all-errors -C - --connect-timeout 20 "https://softwaredownload.futustatic.com/moomoo_OpenD_${OPEND_VERSION}_Ubuntu18.04.tar.gz" -o /tmp/opend.tar.gz; \
       fi \
    && echo "${OPEND_SHA256}  /tmp/opend.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/opend.tar.gz -C /tmp/opend_unpack \
    && opend_binary="$(find /tmp/opend_unpack -type f -name OpenD -print -quit)" \
    && test -n "$opend_binary" \
    && cp -a "$(dirname "$opend_binary")/." /opt/moomooOpenD/ \
    && rm -rf /tmp/opend.tar.gz /tmp/opend_unpack \
    && chmod +x /opt/moomooOpenD/OpenD

# ---------------------------------------------------------------------------
# The MCP server.
# ---------------------------------------------------------------------------
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

COPY deploy/trust/harden_ca.py /opt/security/harden_ca.py

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1
# Ubuntu 22.04's own Python is 3.10; the image this replaces ran 3.12, so uv
# fetches one rather than quietly dropping two minor versions. Pinned to the
# patch release, not the 3.12 series: uv resolves a series to whatever is newest
# at build time, which would make the same commit build a different interpreter
# next month. uv.lock covers everything above it.
ENV UV_PYTHON_DOWNLOADS=automatic
# Reuse the supported PBS runtime verified in the earlier security work.
# Its OpenSSL is independent of Ubuntu libssl3 and OpenD's bundled libraries.
ENV UV_PYTHON=3.12.15
ENV UV_PYTHON_PREFERENCE=only-managed
# Not the default (~/.local/share/uv/python): the build runs as root and the
# container does not, and a 0700 /root would leave the venv pointing at an
# interpreter the service user cannot execute.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python

# Install dependencies first for optimal Docker layer caching
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project \
    && /app/.venv/bin/python /opt/security/harden_ca.py

# Copy source tree and install project cleanly (non-editable in production image)
COPY src/ ./src/
# A missing build argument keeps developer/local builds explicitly unknown.
# This file is generated inside the build, never copied from the host context.
ARG SOURCE_REVISION=""
RUN if [ -n "${SOURCE_REVISION}" ]; then \
      /app/.venv/bin/python ./src/moomoo_mcp/build_identity.py --source-revision "${SOURCE_REVISION}"; \
    fi \
    && uv sync --frozen --no-dev --no-editable \
    && /app/.venv/bin/python /opt/security/harden_ca.py

# Put the venv first on PATH
ENV PATH="/app/.venv/bin:$PATH"

# ---------------------------------------------------------------------------
# One unprivileged identity for both processes. It must stay uid 10001 at
# /home/opend: the opend-data volume was written by that user at that path, and
# a mismatch would leave OpenD looking at what it reads as an empty directory
# and demanding a fresh device authorization over SMS.
# ---------------------------------------------------------------------------
RUN groupadd -g 10001 opend \
    && useradd -u 10001 -g opend -m -d /home/opend -s /bin/bash opend \
    && mkdir -p /home/opend/.com.moomoo.OpenD /var/lib/moomoo-mcp/data /app/real-execution-data /var/lib/moomoo/trading-controls \
    && chown -R opend:opend /opt/moomooOpenD /home/opend /app /var/lib/moomoo-mcp/data /var/lib/moomoo/trading-controls \
    && chmod -R a+rX /opt/uv-python

USER opend
ENV HOME=/home/opend
ENV LD_LIBRARY_PATH=/opt/moomooOpenD
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000
# The gateway answers on container loopback only and is published nowhere, so
# 8000 is the only port this image offers anyone.
EXPOSE 8000

CMD ["moomoo-api-mcp-supervisor"]
