import os
import shlex
import subprocess
import pathlib
import threading
import time
import datetime as dt
import html
from uuid import uuid4
from typing import Tuple, Dict, Any, Optional, List

from flask import Flask, request, jsonify, send_from_directory, Response

# === Konfiguracija iz okoline (možeš menjati u docker-compose) ===
DOWNLOAD_ROOT = os.environ.get("DOWNLOAD_ROOT", "/data/ytpldl/work")
PUBLIC_DOWNLOADS = os.environ.get("PUBLIC_DOWNLOADS", "/app/public/downloads")
COOKIES = "/app/cookies.txt"  # ako ne postoji, yt-dlp će probati bez njih

# === Flask app ===
app = Flask(__name__)

# === In-memory store za job-ove (volatile; resetuje se restartom procesa) ===
jobs_lock = threading.Lock()
jobs: Dict[str, Dict[str, Any]] = {}

# === Utility ===
def ensure_dir(path: str) -> None:
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)

def run_cmd(cmd: List[str], log_fp) -> Tuple[int, str]:
    """Pokreće komandu, loguje stdout/stderr u log fajl i vraća (rc, full_log_str)."""
    pretty = " ".join(shlex.quote(c) for c in cmd)
    log_fp.write(f">> {pretty}\n")
    log_fp.flush()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines = []
    for line in p.stdout:
        output_lines.append(line)
        log_fp.write(line)
        log_fp.flush()
    rc = p.wait()
    return rc, "".join(output_lines)

def ytdlp_download(url: str, out_dir: str, no_playlist: bool, log_fp) -> Tuple[int, str]:
    """
    Fallback na više YouTube klijenata zbog SABR promena:
      ios -> web_creator -> tv_embedded -> web
    Vraća (rc, last_log). rc==0 smatra se uspehom.
    """
    ensure_dir(out_dir)

    base = [
        "yt-dlp",
        "--no-continue",
        "--no-part",
        "--no-mtime",
        "--ignore-errors",
        "--skip-unavailable-fragments",
        "--add-metadata",
        "--embed-thumbnail",
        "--restrict-filenames",
        "--concurrent-fragments", "1",
        "-x", "--audio-format", "mp3",
        "-f", "bestaudio/best",
        "-o", f"{out_dir}/%(playlist_title,playlist)s/%(playlist_index,02d)s-%(title).200B.%(ext)s",
    ]

    if os.path.exists(COOKIES):
        base += ["--cookies", COOKIES]

    if no_playlist:
        base.append("--no-playlist")

    clients = ["ios", "web_creator", "tv_embedded", "web"]

    last_log = ""
    for c in clients:
        cmd = base + ["--extractor-args", f"youtube:player_client={c}", url]
        rc, log = run_cmd(cmd, log_fp)
        last_log = log

        # Heuristika uspeha:
        #  - rc == 0 i pojavljuje se Destination/Merging/Deleting original file u logu
        #  - ili log govori da je fajl već skinut
        if rc == 0 and any(token in log for token in ("Destination:", "Merging formats", "Deleting original file")):
            return 0, log
        if any(token in log for token in ("has already been downloaded", "100% of")):
            return 0, log

        # Ako je SABR 'images only' ili 'requested not available', probaj sledeći klijent
        if ("Only images are available" in log) or ("Requested format is not available" in log):
            log_fp.write(f"[info] Fallback: trying next client after '{c}'\n")
            log_fp.flush()
            continue

        # Ako nije eksplicitno SABR, ali rc != 0, svejedno pokušaj sledeći
        log_fp.write(f"[warn] Non-zero exit with client '{c}', trying next…\n")
        log_fp.flush()

    return 1, last_log

def safe_tail(path: str, max_bytes: int = 32_768) -> str:
    """Vrati poslednjih max_bytes iz fajla (za status)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            chunk = f.read().decode(errors="replace")
            if start > 0:
                return "...\n" + chunk
            return chunk
    except FileNotFoundError:
        return ""

def format_bytes(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"

def list_mp3_files(root: str) -> List[Dict[str, str]]:
    out = []
    for base, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(".mp3"):
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                out.append({"path": rel, "size": format_bytes(size)})
    out.sort(key=lambda x: x["path"])
    return out

# === Worker koji izvršava download job ===
def worker(job_id: str) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return

    url = job["url"]
    no_playlist = job["no_playlist"]
    out_dir = job["out_dir"]
    log_path = job["log_path"]

    ensure_dir(os.path.dirname(log_path))

    with open(log_path, "a", encoding="utf-8") as log_fp:
        log_fp.write(f"Spreman.\nPokrenut job: {job_id}\n")
        log_fp.write(f"🍪 Cookiefile: {COOKIES if os.path.exists(COOKIES) else '(nema, nastavljam bez cookies)'}\n")
        log_fp.write(f"🎵 URL: {url}\n")
        log_fp.flush()

        rc, _ = ytdlp_download(url=url, out_dir=out_dir, no_playlist=no_playlist, log_fp=log_fp)

        with jobs_lock:
            job = jobs.get(job_id)
            if job:
                job["status"] = "done" if rc == 0 else "error"
                job["finished_at"] = dt.datetime.utcnow().isoformat() + "Z"

# === Routes ===
@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/")
def index():
    # Minimal UI – submit forma i lista fajlova
    files = list_mp3_files(PUBLIC_DOWNLOADS)
    rows = "\n".join(
        f"<tr><td><a href='/public/{html.escape(f['path'])}' target='_blank'>{html.escape(f['path'])}</a></td><td>{html.escape(f['size'])}</td></tr>"
        for f in files
    )
    return Response(
        f"""<!doctype html>
<html lang="sr">
<head><meta charset="utf-8"><title>ytpldl</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font-family:system-ui,Arial,sans-serif;margin:24px;}}
input[type=text]{{width:100%;padding:10px;border:1px solid #ccc;border-radius:10px}}
button{{padding:10px 16px;border:1px solid #ccc;border-radius:10px;background:#f5f5f5;cursor:pointer}}
table{{width:100%;border-collapse:collapse;margin-top:20px}}
td,th{{padding:8px;border-bottom:1px solid #eee;text-align:left}}
.small{{color:#666;font-size:.9rem}}
.card{{border:1px solid #eee;border-radius:16px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.04)}}
</style>
</head>
<body>
  <h1>YT Playlist → MP3 (SABR-ready)</h1>
  <div class="card">
    <form method="post" action="/api/download">
      <label> YouTube URL:</label><br/>
      <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." required>
      <p><label><input type="checkbox" name="no_playlist" value="1"> Skini samo ovaj video (ne celu playlistu)</label></p>
      <button type="submit">Pokreni preuzimanje</button>
    </form>
    <p class="small">Fajlovi idu u <code>{html.escape(PUBLIC_DOWNLOADS)}</code>. Ako URL sadrži i <code>list=</code>, po difoltu se skida cela plejlista.</p>
  </div>

  <h2>Preuzeti fajlovi</h2>
  <table>
    <thead><tr><th>Fajl</th><th>Veličina</th></tr></thead>
    <tbody>{rows or "<tr><td colspan='2' class='small'>Nema fajlova još.</td></tr>"}</tbody>
  </table>
</body>
</html>""",
        mimetype="text/html",
    )

@app.post("/api/download")
def api_download():
    url = request.form.get("url") or (request.json or {}).get("url")
    if not url:
        return jsonify({"error": "Nedostaje 'url'"}), 400

    no_playlist = False
    if request.is_json:
        no_playlist = bool((request.json or {}).get("no_playlist"))
    else:
        no_playlist = request.form.get("no_playlist") == "1"

    job_id = uuid4().hex[:8]
    out_dir = os.path.join(PUBLIC_DOWNLOADS, "yt")
    logs_dir = os.path.join(PUBLIC_DOWNLOADS, "jobs")
    ensure_dir(out_dir)
    ensure_dir(logs_dir)
    log_path = os.path.join(logs_dir, f"{job_id}.log")

    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "url": url,
            "no_playlist": no_playlist,
            "status": "running",
            "started_at": dt.datetime.utcnow().isoformat() + "Z",
            "finished_at": None,
            "out_dir": out_dir,
            "log_path": log_path,
        }

    t = threading.Thread(target=worker, args=(job_id,), daemon=True)
    t.start()

    # Ako je forma, redirectuj na status; ako je JSON, vrati payload
    if request.content_type and request.content_type.startswith("application/json"):
        return jsonify({"job_id": job_id, "status_url": f"/api/status/{job_id}"})
    else:
        return Response(
            f"""<!doctype html><meta charset="utf-8">
<p>Pokrenut job: <b>{job_id}</b></p>
<p>Prati status: <a href="/api/status/{job_id}" target="_blank">/api/status/{job_id}</a></p>
<p><a href="/">← Nazad</a></p>""",
            mimetype="text/html",
        )

@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        # Ako nema u memoriji, pokušaj bar log fajl da prikažeš
        log_path = os.path.join(PUBLIC_DOWNLOADS, "jobs", f"{job_id}.log")
        tail = safe_tail(log_path)
        return jsonify({"job_id": job_id, "status": "unknown", "tail": tail})

    tail = safe_tail(job["log_path"])
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "log_path": job["log_path"].replace("\\", "/"),
        "tail": tail,
    })

@app.get("/downloads/")
def list_downloads():
    files = list_mp3_files(PUBLIC_DOWNLOADS)
    return jsonify({"root": PUBLIC_DOWNLOADS, "files": files})

@app.get("/public/<path:filename>")
def public_file(filename: str):
    # Služi statičke fajlove iz PUBLIC_DOWNLOADS (MP3, logovi, itd.)
    return send_from_directory(PUBLIC_DOWNLOADS, filename, as_attachment=False)

# === Local dev ===
if __name__ == "__main__":
    ensure_dir(PUBLIC_DOWNLOADS)
    app.run(host="0.0.0.0", port=8000, debug=True)
