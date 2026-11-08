# psc-agent: web app by default, every CLI command through the same image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[web]"
COPY tests ./tests

# Not root: the container writes only to /data/* mounts and /runs.
RUN useradd --create-home --uid 1000 psc && mkdir -p /data /runs && chown psc /data /runs
USER psc
WORKDIR /data

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:5000/api/status', timeout=4)"

# The image has no login: bind 0.0.0.0 inside, and publish the port on 127.0.0.1 only (see docker-compose.yml).
ENTRYPOINT ["python", "-m", "psc_agent"]
CMD ["serve", "--root", "/data", "--host", "0.0.0.0", "--skills", "/data/skills", "--runs", "/runs"]
