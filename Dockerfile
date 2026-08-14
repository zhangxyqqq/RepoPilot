FROM python:3.12.5-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u2 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.3.2

RUN groupadd --gid 10001 repopilot \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /home/repopilot repopilot

COPY src/repopilot/sandbox/sandbox_runner.py /opt/repopilot/sandbox_runner.py

USER 10001:10001
WORKDIR /workspace

CMD ["sleep", "infinity"]
