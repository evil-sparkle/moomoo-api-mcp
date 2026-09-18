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

# ca-certificates/curl/tar/gzip fetch OpenD; libssl-dev is its runtime dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    gzip \
    libssl-dev \
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
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

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
ENV UV_PYTHON=3.12.13
ENV UV_PYTHON_PREFERENCE=only-managed
# Not the default (~/.local/share/uv/python): the build runs as root and the
# container does not, and a 0700 /root would leave the venv pointing at an
# interpreter the service user cannot execute.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python

# Install dependencies first for optimal Docker layer caching
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source tree and install project cleanly (non-editable in production image)
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

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
    && mkdir -p /home/opend/.com.moomoo.OpenD \
    && chown -R opend:opend /opt/moomooOpenD /home/opend /app \
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
