# Dockerfile - Python base from AWS public ECR + ffmpeg + gunicorn
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # defaulti (biće prepisani iz docker-compose.yml okolinom)
    DOWNLOAD_ROOT=/data/ytpldl/work \
    PUBLIC_DOWNLOADS=/app/public/downloads

# OS deps + ffmpeg (yt-dlp koristi ffmpeg/ffprobe)
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

# Ne definisemo VOLUME ovde; koristiš host bind mountove u compose-u
EXPOSE 8000

# (opciono) HEALTHCHECK – ako dodaš /health u app.py
# HEALTHCHECK --interval=30s --timeout=5s --retries=10 CMD wget -qO- http://127.0.0.1:8000/health || exit 1

# Production entry - gunicorn (duži timeout jer se preuzimaju plejliste/zip)
CMD ["python", "-m", "gunicorn", \
     "--workers", "4", "--threads", "4", "--timeout", "3600", \
     "--bind", "0.0.0.0:8000", "app:app"]
