# syntax=docker/dockerfile:1
# Dev container: Python 3.14 + all project deps + Claude Code CLI.
#
# Layer order (most stable → least stable):
#   1. Base image + system packages    — changes ~never
#   2. Claude Code CLI                  — changes ~monthly
#   3. uv + Python dev tools           — changes ~monthly
#   4. All project deps (single layer) — changes when any pyproject.toml changes
#   5. Bytecode precompile + dev user  — always runs (cheap)
#
# Source code comes from bind mount — never copied into image.

FROM python:3.14-slim

# 1. System packages (most stable layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        git-lfs \
        just \
        curl \
        ca-certificates \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# 2. Claude Code CLI — the VS Code extension bundles the native binary;
#    postStartCommand symlinks it to a shared PATH location.

# 3. uv (fast pip replacement) + Python dev tools
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system ruff mypy types-requests

# 4. All project deps — copy only pyproject.toml files, create stubs, single install
COPY lol-pipeline-common/pyproject.toml /tmp/common/pyproject.toml
COPY lol-pipeline-common/src/           /tmp/common/src/
COPY lol-pipeline-admin/pyproject.toml            /tmp/admin/pyproject.toml
COPY lol-pipeline-admin-ui/pyproject.toml        /tmp/admin-ui/pyproject.toml
COPY lol-pipeline-champion-stats/pyproject.toml    /tmp/champion-stats/pyproject.toml
COPY lol-pipeline-crawler/pyproject.toml          /tmp/crawler/pyproject.toml
COPY lol-pipeline-delay-scheduler/pyproject.toml  /tmp/delay-scheduler/pyproject.toml
COPY lol-pipeline-discovery/pyproject.toml        /tmp/discovery/pyproject.toml
COPY lol-pipeline-fetcher/pyproject.toml          /tmp/fetcher/pyproject.toml
COPY lol-pipeline-parser/pyproject.toml           /tmp/parser/pyproject.toml
COPY lol-pipeline-player-stats/pyproject.toml     /tmp/player-stats/pyproject.toml
COPY lol-pipeline-recovery/pyproject.toml         /tmp/recovery/pyproject.toml
COPY lol-pipeline-ui/pyproject.toml               /tmp/ui/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    for d in admin admin-ui champion-stats crawler delay-scheduler discovery fetcher parser player-stats recovery ui; do \
      pkg="lol_$(echo $d | tr - _)"; \
      mkdir -p "/tmp/$d/src/$pkg" && touch "/tmp/$d/src/$pkg/__init__.py"; \
    done \
    && uv pip install --system \
         -e "/tmp/common/[dev]" \
         -e /tmp/admin \
         -e /tmp/admin-ui \
         -e /tmp/champion-stats \
         -e /tmp/crawler \
         -e /tmp/delay-scheduler \
         -e /tmp/discovery \
         -e /tmp/fetcher \
         -e /tmp/parser \
         -e /tmp/player-stats \
         -e /tmp/recovery \
         -e /tmp/ui

# 5. Precompile bytecode (all cores) + dev user
RUN python -m compileall -q -j 0 /usr/local/lib/python3.14/site-packages 2>/dev/null || true

ENV PYTHONUNBUFFERED=1
WORKDIR /workspace

RUN useradd -ms /bin/bash devuser \
    && echo "devuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/devuser \
    && mkdir -p /home/devuser/.cache/uv \
    && mkdir -p /home/devuser/.claude \
    && mkdir -p /home/devuser/.local/bin \
    && chown -R devuser:devuser /home/devuser \
    && chown -R devuser:devuser /usr/local/lib/python3.14/site-packages
ENV PATH="/home/devuser/.local/bin:${PATH}"

CMD ["bash"]
