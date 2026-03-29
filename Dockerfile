# ── Stage 1: Build React frontend ────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python runtime ─────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY app.py gunicorn.conf.py ./
COPY routes/ routes/
COPY forecast/ forecast/

# Copy built frontend from stage 1
COPY --from=frontend-build /app/frontend/dist frontend/dist

# Cloud Run sets $PORT; gunicorn.conf.py reads it
ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "app:app", "-c", "gunicorn.conf.py"]
