# Dockerfile - Python base from AWS public ECR + ffmpeg + gunicorn
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system deps and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create downloads dir and expose volume for persistence
RUN mkdir -p /app/downloads
VOLUME ["/app/downloads"]

EXPOSE 8000

# Production entry - gunicorn
CMD ["gunicorn", "--workers", "4", "--threads", "4", "--bind", "0.0.0.0:8000", "app:app"]
