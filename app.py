import os
import re
import uuid
import threading
from collections import deque
from flask import Flask, request, jsonify, Response

# ---------------- CONFIG ----------------
DOWNLOAD_ROOT = os.path.join(os.path.expanduser("~"), "Downloads")
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

jobs = {}  # memorija za aktivne downloade

app = Flask(__name__)

try:
    import yt_dlp
except ModuleNotFoundError:
    raise SystemExit("yt_dlp nije instaliran. Pokreni: python -m pip install yt-dlp Flask")

LOG_FILE_NAME = "failed_downloads.txt"


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
                os.remove(full_path)
                continue
            if new_path != full_path:
                os.rename(full_path, new_path)
                log(f"✅ Preimenovano: {filename} -> {new_name}")


def run_job(job_id: str, playlist_urls: list[str], target_subdir: str, preferred_quality: str = "192"):
    job = jobs[job_id]

    def log(msg: str):
        job["log"].append(msg)
        print(f"[{job_id}] {msg}")

    job["status"] = "running"
    target_folder = os.path.join(DOWNLOAD_ROOT, sanitize_filename(target_subdir) or "yt-dlp-downloads")
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
        with open(failed_log_path, "a", encoding="utf-8") as f:
            f.write(f"{url} - {reason}\n")

    # 🔧 pamti koji fajl se trenutno skida da ne spama
    last_file = {"name": None}

    def hook(d):
        if d["status"] == "downloading" and d.get("filename"):
            title = os.path.basename(d["filename"])
            if title != last_file["name"]:
                last_file["name"] = title
                log(f"▶️ Skidam: {title}")
        elif d["status"] == "finished" and d.get("filename"):
            title = os.path.basename(d["filename"])
            log(f"✅ Skinuto: {title}")
            # odmah proveravamo konverziju
            mp3_path = os.path.splitext(title)[0] + ".mp3"
            log(f"🎧 Konvertujem u {mp3_path}")
            last_file["name"] = None

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(target_folder, "%(title)s.%(ext)s"),
        "restrictfilenames": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": preferred_quality,
            }
        ],
        "ignoreerrors": True,
        "noplaylist": False,
        "ffmpeg_location": ".",  # očekuje ffmpeg.exe i ffprobe.exe u istom folderu
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }

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
        log("✅ Gotovo.")
        job["status"] = "done"
    except Exception as e:
        log(f"💥 Fatal error: {e}")
        job["status"] = "error"


@app.route("/")
def index():
    html = """
    <!doctype html>
    <meta charset="utf-8" />
    <title>yt-dlp Web UI</title>
    <style>
      body{font-family:system-ui,Arial,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}
      h1{margin-bottom:.5rem}
      input,textarea,button{width:100%;padding:.6rem;border:1px solid #ccc;border-radius:10px}
      button{cursor:pointer;margin-top:1rem}
      #log{white-space:pre-wrap;background:#0a0a0a;color:#eaeaea;padding:1rem;
           border-radius:10px;height:300px;overflow-y:auto;font-size:15px;}
      .muted{color:#666}
    </style>
    <h1>yt-dlp Web UI</h1>
    <p class="muted">⚠️ Poštuj autorska prava i uslove korišćenja YouTube-a.</p>
    <label>Playlist ili video URL-ovi (jedan po liniji)</label>
    <textarea id="urls" rows="6"
      placeholder="https://www.youtube.com/watch?v=...&#10;https://www.youtube.com/playlist?list=..."></textarea>
    <label>Folder (biće kreiran u Downloads)</label>
    <input id="folder" value="Play Lista 1"/>
    <label>MP3 kvalitet (kbps: 128/192/256/320)</label>
    <input id="quality" value="192"/>
    <button id="startBtn">Pokreni</button>
    <h2>Log</h2>
    <div id="log">Spreman.</div>
    <script>
      const logBox = document.getElementById('log');
      const apiBase = window.location.origin;
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
        const res = await fetch(`${apiBase}/start`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({urls,folder,quality})
        });
        const data = await res.json();
        currentJob = data.job_id;
        appendLog(`Pokrenut job: ${currentJob}`);
        startPolling();
      });

      function startPolling(){
        if(pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(async ()=>{
          if(!currentJob) return;
          const res = await fetch(`${apiBase}/logs/${currentJob}`);
          if(!res.ok) return;
          const data = await res.json();
          const newLines = data.log.slice(lastLines);
          newLines.forEach(line=>appendLog(line));
          lastLines = data.log.length;
          if(data.status==='done'||data.status==='error'){
            clearInterval(pollTimer);
            appendLog(`\\nStatus: ${data.status}`);
            if(data.target_folder) appendLog(`\\nOutput: ${data.target_folder}`);
          }
        }, 1000);
      }
    </script>
    """
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
    jobs[job_id] = {"status": "queued", "log": deque(maxlen=5000), "target_folder": None, "failed_log_path": None}

    t = threading.Thread(target=run_job, args=(job_id, urls, folder, quality), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/logs/<job_id>")
def get_logs(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Nepoznat job."}), 404
    return jsonify({
        "status": job["status"],
        "log": list(job["log"]),
        "target_folder": job.get("target_folder"),
        "failed_log_path": job.get("failed_log_path"),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
