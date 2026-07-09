# Backend image for Railway (ADR-0004 deployment). The laufwise engine is a separate public repo
# (not on PyPI), so it is installed from git — the same source CI uses. Migrations run at container
# start (alembic upgrade head is idempotent), then uvicorn serves on Railway's $PORT.
FROM python:3.12-slim

WORKDIR /app

# git: to pip-install the engine from its repo. --no-install-recommends keeps the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install the engine first (its own layer, cached across app-code changes).
RUN pip install --no-cache-dir "git+https://github.com/dfadeeff/laufwise.git"

# Then the app + its deps (no [dev] — prod doesn't need pytest/ruff). Source must be present for
# the editable install, so copy before installing.
COPY . .
RUN pip install --no-cache-dir -e .

# Railway injects $PORT. Run migrations, then start the API. Shell form so $PORT + && expand.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
