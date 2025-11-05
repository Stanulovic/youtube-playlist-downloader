# yt-dlp-web (simple production-style)

## Lokalno testiranje (Docker)
# build and run (first time)
docker compose build
docker compose up -d

# aplikacija dostupna na:
# http://localhost (nginx reverse-proxy) --> proxies to gunicorn:8000

# logs:
docker compose logs -f app
docker compose logs -f nginx

# stop
docker compose down

## Na EC2 (brzi test bez ECR)
1) Priprema instance (Ubuntu)
   sudo apt update && sudo apt install -y docker.io docker-compose

2) Kloniraj repo u /home/ubuntu/yt-dlp-web i pokreni:
   docker compose up -d --build

3) U AWS Console -> Security Groups -> Security group of EC2:
   - Open port 80 (HTTP) i port 22 (SSH) po potrebi.

## Alternativa: bez nginx (direktno na port 8000)
   docker run -p 8000:8000 <image>

## Napomene za production:
- Gunicorn + Nginx su solidna osnova, ali:
  - postavi `DEBUG=False` u app (ukloni debug=True)
  - ograniči broj paralelnih jobova
  - dodaj auth / rate-limit pre nego što izlažeš javno
  - obrati pažnju na DMCA / YouTube ToS

