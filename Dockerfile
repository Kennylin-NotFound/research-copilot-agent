# syntax=docker/dockerfile:1.7
FROM python:3.11.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.release.txt /app/requirements.release.txt
COPY requirements.release.lock.txt /app/requirements.release.lock.txt
RUN python -m pip install --upgrade pip==26.0.1 \
    && python -m pip install --requirement /app/requirements.release.lock.txt \
    && groupadd --system --gid 10001 copilot \
    && useradd --system --uid 10001 --gid copilot --home-dir /app --shell /usr/sbin/nologin copilot \
    && mkdir -p /var/lib/research-copilot \
    && chown -R copilot:copilot /app /var/lib/research-copilot

COPY --chown=copilot:copilot agent /app/agent
COPY --chown=copilot:copilot domain /app/domain
COPY --chown=copilot:copilot product /app/product
COPY --chown=copilot:copilot config.py /app/config.py

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "product.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
