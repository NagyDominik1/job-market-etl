# Image for the ETL pipeline (extract -> transform -> load).
# Build and run it via Docker Compose:  docker compose up --build

# Official slim Python image, pinned to the 3.14 minor version
# ("slim" = Debian with only the essentials, so the image stays small).
FROM python:3.14-slim

# PYTHONDONTWRITEBYTECODE: don't write .pyc cache files (useless in a container).
# PYTHONUNBUFFERED: send output straight to the terminal/log, without buffering,
#                   so `docker compose logs` shows lines immediately.
# PYTEST_ADDOPTS: turn off pytest's cache folder; the non-root user can't
#                 write to /app, and a cache is pointless in a throwaway container.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider"

# All following commands run in /app (created automatically).
WORKDIR /app

# Layer caching: Docker builds the image step by step and caches every step
# ("layer"). If a step's input hasn't changed, Docker reuses the cached result.
# We copy ONLY requirements.txt first and install the packages. Our code
# changes often, but the requirements rarely do, so this slow pip install is
# reused from the cache on most rebuilds. Only the COPY steps below re-run.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Security: don't run as root. Create a normal user "app" and give it the
# data/ folder, the only place the pipeline writes to. (This step is also
# placed before the code COPY, so it stays cached when the code changes.)
RUN useradd --create-home app \
    && mkdir -p /app/data \
    && chown app:app /app/data

# Now copy our code (these layers are rebuilt whenever the code changes).
# The files are owned by root, so the "app" user can read but not modify them.
COPY src/ ./src/
COPY tests/ ./tests/

# From here on (including when the container runs), use the "app" user.
USER app

# Default command when the container starts: run the pipeline once.
# (Another command can be given instead, e.g. `docker compose run --rm pipeline python -m pytest`.)
CMD ["python", "-m", "src.main"]
