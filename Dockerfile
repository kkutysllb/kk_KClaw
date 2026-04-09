FROM debian:13.4

# Install system dependencies in one layer, clear APT cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 python3-pip ripgrep ffmpeg gcc python3-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY . /opt/kclaw
WORKDIR /opt/kclaw

# Install Python and Node dependencies in one layer, no cache
RUN pip install --no-cache-dir -e ".[all]" --break-system-packages && \
    npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    cd /opt/kclaw/scripts/whatsapp-bridge && \
    npm install --prefer-offline --no-audit && \
    npm cache clean --force

WORKDIR /opt/kclaw
RUN chmod +x /opt/kclaw/docker/entrypoint.sh

ENV KCLAW_HOME=/opt/data
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/opt/kclaw/docker/entrypoint.sh" ]
