# Build context = repository root:  docker build -f server/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LBA_SKILL_DIR=/app/skills/looks-busy-agent
WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt
# Single source of truth for report policy and the IMAP collector lives in the skill.
COPY skills/looks-busy-agent/references/report-policy.md /app/skills/looks-busy-agent/references/report-policy.md
COPY skills/looks-busy-agent/scripts/collect_email.py   /app/skills/looks-busy-agent/scripts/collect_email.py
COPY server/lba /app/server/lba
WORKDIR /app/server
RUN useradd -r -u 10001 lba && mkdir -p /data && chown lba /data
USER lba
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD ["python", "-m", "lba", "healthz"]
CMD ["python", "-m", "lba", "serve"]
