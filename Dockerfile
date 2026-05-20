FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-dev \
        libvirt-dev pkg-config gcc \
        openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN if ! getent group ${GID} >/dev/null; then groupadd -g ${GID} dev; fi \
 && if ! id -u ${UID} >/dev/null 2>&1; then useradd -m -u ${UID} -g ${GID} -s /bin/bash dev; fi

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN python3 -m venv ${VIRTUAL_ENV} \
 && chown -R ${UID}:${GID} ${VIRTUAL_ENV}

USER ${UID}:${GID}
RUN pip install --upgrade pip \
 && pip install \
        "libvirt-python>=10.0.0" \
        "mcp[cli]>=1.6.0"

USER root
WORKDIR /opt/libvirt-mcp
COPY --chown=${UID}:${GID} pyproject.toml ./
COPY --chown=${UID}:${GID} config.example.toml ./
COPY --chown=${UID}:${GID} libvirt_mcp/ ./libvirt_mcp/
USER ${UID}:${GID}
RUN pip install --no-deps .

WORKDIR /
CMD ["python", "-m", "libvirt_mcp"]
