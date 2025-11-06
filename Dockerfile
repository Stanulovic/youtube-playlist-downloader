# Dockerfile - Python base from AWS public ECR + ffmpeg + gunicorn
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# OS deps + ffmpeg (yt-dlp često treba ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Brži/otporniji pip
RUN python -m pip install --upgrade pip

# Zavisnosti
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Aplikacija
COPY . .

# Download direktorijum
RUN mkdir -p /app/downloads && \
    adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

VOLUME ["/app/downloads"]
EXPOSE 8000

# Po želji health endpoint u app-u: /healthz pa dodaš HEALTHCHECK
# HEALTHCHECK --interval=30s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:8000/healthz || exit 1

# Production entry - gunicorn
CMD ["gunicorn", "--workers", "4", "--threads", "4", "--bind", "0.0.0.0:8000", "app:app"]
