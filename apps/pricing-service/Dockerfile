# syntax=docker/dockerfile:1

# ---- build stage: install dependencies into a virtualenv -------------------------
FROM python:3.15-slim AS build
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ---- runtime stage: only Python + the venv + our code ----------------------------
FROM python:3.15-slim AS runtime
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY app/ ./app/
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8080 50051
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "20"]
