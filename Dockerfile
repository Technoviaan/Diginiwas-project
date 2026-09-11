# Niwas AI chat API - production image.
# Build and run with docker compose; see DEPLOY.md.

# Same Python the service was developed and tested on.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/app

RUN groupadd --system app && useradd --system --gid app --home-dir /srv/app app

# Dependencies first, so code changes don't reinstall them.
COPY requirements.lock .
RUN pip install -r requirements.lock

COPY app ./app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status != 200)"

# --workers 1 on purpose: conversation memory and rate-limit counts live in
# this process, so a second worker would split them.
# --proxy-headers: take the client IP from the reverse proxy's
# X-Forwarded-For, so rate limiting counts real users. "*" is safe because
# the port is only reachable from the proxy (see docker-compose.yml).
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-graceful-shutdown", "20"]
