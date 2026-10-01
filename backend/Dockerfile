# ComplyNexus backend — FastAPI Agentic RAG service
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
# --trusted-host flags tolerate corporate TLS-interception proxies (e.g. Zscaler)
# that break SSL verification against PyPI.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    --trusted-host pypi.python.org \
    && pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    --trusted-host pypi.python.org \
    -r requirements.txt

# Application code.
COPY app ./app

EXPOSE 8080

# Honour BACKEND_PORT if provided, default 8080.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT:-8080}"]
