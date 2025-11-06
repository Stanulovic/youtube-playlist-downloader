import os
import re
import uuid
import zipfile
import shutil
import traceback
import threading
from collections import deque
from urllib.parse import quote
from flask import Flask, request, jsonify, Response, send_from_directory

# ================== CONFIG ==================
DOWNLOAD_ROOT = os.environ.get("DOWNLOAD_ROOT", os.path.join(os.path.expanduser("~"), "Downloads"))
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

PUBLIC_DOWNLOADS = os.environ.get("PUBLIC_DOWNLOADS", "/app/public/downloads")
os.makedirs(PUBLIC_DOWNLOADS, exist_ok=True)

# Putanja do cookies fajla unutar containera (može i preko ENV)
COOKIE_FILE = os.environ.get("YTDLP_COOKIEFILE", "/app/cookies.txt")

LOG_FILE_NAME = "failed_downloads.txt"
jobs = {}

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # lepši UTF-8 JSON

try:
    import yt_dlp
except ModuleNotFoundError:
    raise SystemExit("yt_dlp nije instaliran. Pokreni: python -m pip install yt-dlp Flask")

# ================== HELPERS ==================
def sanitize_filename(name: str) -> str:
    return re.sub(r"[\\/*?:\"<>|]", "", name).strip()

def clean_title(title: str) -> str:
    return re.sub(r"[\[\(].*?[\]\)]", "", title).strip()

def parse_artist_and_title(full_title: str):
    if "-" in full_title:
        parts = full_title.split("-", 1)
        artist = sanitize_filename(parts[0].strip())
        title = sanitize_filename(clean_title(parts[1].strip()))
        return artist, title
    else:
        return None, sanitize_filename(clean_title(full_title))

def post_process_filenames(target_folder: str, log):
    for filename in os.listdir(target_folder):
        if filename.lower().endswith(".mp3"):
            full_path = os.path.join(target_folder, filename)
            name_part = os.path.splitext(filename)[0]
            artist, title = parse_artist_and_title(name_part)
            new_name = f"{artist} - {title}.mp3" if artist else f"{title}.mp3"
            new_path = os.path.join(target_folder, new_name)
            if os.path.exists(new_path) and new_path != full_path:
                log(f"⚠️ Duplikat: {new_name} već postoji. Brišem {filename}")
                try:
                    os.remove(full_path)
                except Exception as e:
                    log(f"⚠️ Ne mogu da obrišem {filename}: {e}")
                continue
            if new_path != full_path:
                try:
                    os.rename(full_path, new_path)
                    log(f"✅ Preimenovano: {filename} -> {new_name}")
                except Exception as e:
                    log(f"⚠️ Nije moguće preimenovati {filename}: {e}")

# ================== yt-dlp logger ==================
class YDLLogger:
    def __init__(self, logfn): 
        self.log = logfn
    def debug(self, msg):
        if any(k in msg for k in ("Downloading", "Destination", "has already been downloaded", "Extracting")):
            self.log(msg)
    def warning(self, msg): 
        self.log(f"⚠️ {msg}")
    def error(self, msg): 
        self.log(f"❌ {msg}")

# ================== CORE JOB ==================
def run_job(job_id: str, playlist_urls: list[str], target_subdir: str, preferred_quality: str = "192"):
    job = jobs[job_id]

    def log(msg: str):
        job["log"].append(msg)
        print(f"[{job_id}] {msg}")

    job["status"] = "running"
    safe_subdir = sanitize_filename(target_subdir) or "yt-dlp-downloads"
    target_folder = os.path.join(DOWNLOAD_ROOT, safe_subdir)
    os.makedirs(target_folder, exist_ok=True)
    job["target_folder"] = target_folder

    failed_log_path = os.path.join(target_folder, LOG_FILE_NAME)
    job["failed_log_path"] = failed_log_path
    if os.path.exists(failed_log_path):
        try:
            os.remove(failed_log_path)
        except Exception:
            pass

    def log_failed(url, reason):
        try:
            with open(failed_log_path, "a", encoding="utf-8") as f:
                f.write(f"{url} - {reason}\n")
        except Exception as e:
            log(f"⚠️ Ne mogu da upišem u {LOG_FILE_NAME}: {e}")

    last_file = {"name": None}

    def hook(d):
        try:
            if d["status"] == "downloading" and d.get("filename"):
                title = os.path.basename(d["filename"])
                if title != last_file["name"]:
                    last_file["name"] = title
                    log(f"▶️ Skidam: {title}")
            elif d["status"] == "finished" and d.get("filename"):
                title = os.path.basename(d["filename"])
                log(f"✅ Skinuto: {title}")
                last_file["name"] = None
        except Exception:
            pass

    def pp_hook(d):
        if d.get("status") == "finished" and d.get("postprocessor") == "FFmpegExtractAudio":
            try:
                src = os.path.basename(d.get("info_dict", {}).get("_filename", ""))  # m4a/webm
                base = os.path.splitext(src)[0] + ".mp3"
                log(f"🎧 Konvertovano: {base}")
            except Exception:
                log("🎧 Konverzija završena.")

    # Preflight: obavezan ffmpeg
    if not shutil.which("ffmpeg"):
        log("❌ FFmpeg nije instaliran. Instaliraj ffmpeg (dnf/apt/apk) i probaj ponovo.")
        job["status"] = "error"
        return

    # yt-dlp opcije
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(target_folder, "%(title)s.%(ext)s"),
        "restrictfilenames": False,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": preferred_quality}
        ],
        "keepvideo": True,
        "ignoreerrors": False,
        "noplaylist": False,
        "logger": YDLLogger(log),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        # stabilizacija može pomoći, ali cookies su ključni za "sign in" blok
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    # Cookie podrška – zahtevana za "Sign in to confirm you're not a bot"
    if os.path.exists(COOKIE_FILE):
        ydl_opts["cookiefile"] = COOKIE_FILE
        log(f"🍪 Koristim cookiefile: {COOKIE_FILE}")
    else:
        log(f"⚠️ Cookie fajl nije pronađen: {COOKIE_FILE}. Za neke linkove biće potreban.")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for url in playlist_urls:
                log(f"🎵 Pokrećem preuzimanje: {url}")
                try:
                    ydl.download([url])
                except Exception as e:
                    log(f"❌ Greška: {url} ({e})")
                    log_failed(url, str(e))

        log("🔧 Obrada fajlova...")
        post_process_filenames(target_folder, log)

        # Skupljanje fajlova za ZIP
        files_to_zip = []
        for root, _, files in os.walk(target_folder):
            for f in files:
                if f.lower().endswith((".mp3", ".m4a", ".webm")):
                    files_to_zip.append(os.path.join(root, f))

        if not files_to_zip:
            log("❌ Nema generisanih audio fajlova (.mp3/.m4a/.webm). Proveri linkove i/ili ffmpeg/cookies.")
            job["status"] = "error"
            return

        # ZIP u PUBLIC_DOWNLOADS
        zip_name = f"{safe_subdir}.zip"
        tmp_zip_path = os.path.join(target_folder, zip_name)
        try:
            with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for full in files_to_zip:
                    arc = os.path.relpath(full, start=target_folder)
                    zf.write(full, arcname=arc)
        except Exception as e:
            log(f"⚠️ Neuspešno pakovanje ZIP-a: {e}")
            job["status"] = "error"
            return

        final_zip_path = os.path.join(PUBLIC_DOWNLOADS, zip_name)
        try:
            os.replace(tmp_zip_path, final_zip_path)
        except OSError:
            try:
                shutil.copy2(tmp_zip_path, final_zip_path)
                os.remove(tmp_zip_path)
            except Exception as e:
                log(f"⚠️ Ne mogu da kopiram ZIP u public: {e}")
                job["status"] = "error"
                return

        public_link = f"/ytpldl/downloads/{quote(zip_name)}"
        job["public_zip"] = public_link
        log(f"📦 ZIP spreman: {public_link}")

        log("✅ Gotovo.")
        job["status"] = "done"
    except Exception as e:
        log(f"💥 Fatal error: {e}")
        traceback.print_exc()
        job["status"] = "error"

# ================== ROUTES ==================
@app.route("/")
def index():
    html = """<!doctype html>
    <meta charset="utf-8" />
    <title>YTPLDL</title>
    <style>
      body{font-family:system-ui,Arial,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem}
      input,textarea,button{width:100%;padding:.6rem;border:1px solid #ccc;border-radius:10px}
      button{cursor:pointer;margin-top:.5rem}
      #log{white-space:pre-wrap;background:#0a0a0a;color:#eaeaea;padding:1rem;
           border-radius:10px;height:300px;overflow-y:auto;font-size:15px;}
      .muted{color:#666}
      a.btn{display:inline-block;margin-top:.5rem;font-weight:600}
    </style>
    <h1>YouTube Play List Downloader</h1>
    <p class="muted">⚠️ Poštuj autorska prava i uslove korišćenja YouTube-a.</p>
    <label>Playlist ili video URL-ovi (jedan po liniji)</label>
    <textarea id="urls" rows="6"
      placeholder="https://www.youtube.com/playlist?list=...&#10;https://www.youtube.com/watch?v=..."></textarea>
    <label>Upiši naziv playliste/ZIP-a</label>
    <input id="folder" value=""/>
    <label>MP3 kvalitet (kbps: 128/192/256/320)</label>
    <input id="quality" value="192"/>
    <button id="startBtn">Pokreni</button>
    <div id="log">Spreman.</div>
    <script>
      const logBox = document.getElementById('log');
      const basePath = window.location.pathname.replace(/\\/$/, '');
      let currentJob = null;
      let lastLines = 0;
      let pollTimer = null;
      function appendLog(t){
        logBox.textContent += "\\n" + t;
        logBox.scrollTop = logBox.scrollHeight;
      }
      document.getElementById('startBtn').addEventListener('click', async ()=>{
        const urls = document.getElementById('urls').value.split('\\n').map(s=>s.trim()).filter(Boolean);
        const folder = document.getElementById('folder').value.trim() || 'yt-dlp-downloads';
        const quality = document.getElementById('quality').value.trim() || '192';
        const res = await fetch(`${basePath}/start`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({urls,folder,quality})
        });
        const data = await res.json();
        if(data.error){
          appendLog('❌ ' + data.error);
          return;
        }
        currentJob = data.job_id;
        appendLog(`Pokrenut job: ${currentJob}`);
        startPolling();
      });
      function startPolling(){
        if(pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(async ()=>{
          if(!currentJob) return;
          const res = await fetch(`${basePath}/logs/${currentJob}`);
          if(!res.ok) return;
          const data = await res.json();
          const newLines = data.log.slice(lastLines);
          newLines.forEach(line=>appendLog(line));
          lastLines = data.log.length;
          if(data.status==='done'||data.status==='error'){
            clearInterval(pollTimer);
            appendLog(`\\nStatus: ${data.status}`);
            if(data.public_zip){
              appendLog(`\\nZIP: ${data.public_zip}`);
              const a=document.createElement('a');
              a.href=data.public_zip;
              a.textContent='⬇️ Preuzmi ZIP';
              a.className='btn';
              a.download = '';
              logBox.insertAdjacentElement('afterend',a);
            }
          }
        },1000);
      }
    </script>"""
    return Response(html, mimetype="text/html")

@app.route("/start", methods=["POST"])
def start_job():
    data = request.get_json(force=True)
    urls = data.get("urls") or []
    folder = data.get("folder") or "yt-dlp-downloads"
    quality = str(data.get("quality") or "192")

    if not isinstance(urls, list) or not urls:
        return jsonify({"error": "Prosledi makar jedan URL."}), 400

    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"status": "queued", "log": deque(maxlen=8000), "public_zip": None}
    t = threading.Thread(target=run_job, args=(job_id, urls, folder, quality), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})

@app.route("/logs/<job_id>")
def get_logs(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Nepoznat job."}), 404
    return jsonify({"status": job["status"], "log": list(job["log"]), "public_zip": job.get("public_zip")})

@app.route("/ytpldl/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(PUBLIC_DOWNLOADS, filename, as_attachment=True)

@app.errorhandler(Exception)
def handle_all_errors(e):
    trace = traceback.format_exc()
    print("=== INTERNAL ERROR ===")
    print(trace)
    return jsonify({"error": str(e), "type": e.__class__.__name__}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
