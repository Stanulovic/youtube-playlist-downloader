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
      ffmpeg ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Brži/otporniji pip
RUN python -m pip install --upgrade pip

# Odradi upgrade yt-dlp na najnoviju verziju
RUN pip install --upgrade yt-dlp

# Zavisnosti (u requirements.txt OBAVEZNO: Flask, yt-dlp, gunicorn)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Aplikacija + cookies
# (cookies.txt mora biti u root-u projekta pored app.py pre build-a)
COPY . .
# opciono: setuj permisije (ne mora, ali je uredno)
RUN chmod 600 /app/cookies.txt || true

# Ne definišemo VOLUME ovde; koristi se bind mount u compose-u po potrebi
RUN mkdir -p /app/public/downloads /data/ytpldl/work
EXPOSE 8000

# Production entry - gunicorn (duži timeout jer se preuzimaju plejliste/zip)
CMD ["python", "-m", "gunicorn", \
     "--workers", "4", "--threads", "4", "--timeout", "3600", \
     "--bind", "0.0.0.0:8000", "app:app"]

