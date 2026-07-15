FROM python:3.12-slim

WORKDIR /app/backend

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install backend dependencies
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# Copy backend code
COPY backend/ ./

# Copy frontend build output
COPY frontend/dist/ ../frontend/dist/

EXPOSE 8000

ENV FRONTEND_DIR=/app/frontend/dist

CMD ["uv", "run", "python", "main.py"]
