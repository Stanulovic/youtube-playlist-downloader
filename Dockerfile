# Dockerfile - Python base from AWS public ECR + ffmpeg + gunicorn + cookies
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # defaulti (možeš ih prepisati u compose-u)
    DOWNLOAD_ROOT=/data/ytpldl/work \
    PUBLIC_DOWNLOADS=/app/public/downloads \
    # putanja do cookies fajla unutar containera
    YTDLP_COOKIEFILE=/app/cookies.txt

# OS deps + ffmpeg (yt-dlp koristi ffmpeg/ffprobe)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg ca-certificates build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Brži/otporniji pip
RUN python -m pip install --upgrade pip

# === KLJUČNO: yt-dlp nightly (master) zbog SABR promena ===
# (umesto običnog "pip install --upgrade yt-dlp")
RUN pip install --no-cache-dir "yt-dlp @ https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.zip"

# Zavisnosti (u requirements.txt OBAVEZNO: Flask, gunicorn; yt-dlp nije neophodan ovde)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Aplikacija + cookies
# (cookies.txt mora biti u root-u projekta pre build-a, ili montiraj preko volume-a)
COPY . .
# opciono: setuj permisije (ne mora, ali je uredno)
RUN chmod 600 /app/cookies.txt || true

# Pripremi direktorijume
RUN mkdir -p /app/public/downloads /data/ytpldl/work
EXPOSE 8000

# Production entry - gunicorn (duži timeout jer se preuzimaju plejliste/zip)
CMD ["python", "-m", "gunicorn", \
     "--workers", "4", "--threads", "4", "--timeout", "3600", \
     "--bind", "0.0.0.0:8000", "app:app"]
