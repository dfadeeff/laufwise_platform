# Backend image for Railway (ADR-0004 deployment). The laufwise engine is a separate public repo
# (not on PyPI), so it is installed from git — the same source CI uses. Migrations do NOT run here;
# see the CMD note at the bottom for where they run and why.
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

# Chromium + its system libraries for the doctolib headless login (Option A1). `--with-deps` runs
# apt-get for the shared libs Chromium needs; the headless shell (~150 MB) is enough (no full
# browser UI). This is the ~300 MB image cost of getting past friendlyCaptcha.
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Start the API on a FIXED port 8080 (matches the Railway domain's target port). Migrations are
# NOT run here — `alembic upgrade head` on every boot blocks uvicorn if the DB stalls, and re-runs
# on every restart. They run as Railway's `preDeployCommand` (railway.json), which is a separate
# step before traffic switches: a stalled DB fails the deploy instead of hanging the server, and
# the old container keeps serving. That command also seeds runbooks, so publishing a new template
# version needs no manual step; both are idempotent, so re-running them is a no-op.
CMD uvicorn app.main:app --host 0.0.0.0 --port 8080
