#!/usr/bin/env python3
"""
Mojidori — Flask web UI + background job worker.

How the "auto" part works:
  * Every input (a dropped file, a file copied into queue/, a pasted link)
    becomes a Job.
  * One worker thread runs jobs in order, so two runs can never fight over
    the GPU or the same file.
  * A folder watcher adds files that land in queue/ — but only after their
    size has stopped changing, so half-copied recordings are never touched.
  * Auto-start on: new jobs run immediately. Off: they wait for "Start".
  * The browser is just a view: it gets live job updates over one SSE stream,
    and closing the tab doesn't stop anything.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from core import (
    LANGUAGES, PROVIDERS, Cancelled, archive_original, download_audio,
    expand_url, find_urls, get_audio_files, maybe_ping_ntfy, resolve_api_key,
    safe_name, test_api_key, transcribe_file,
)

app = Flask(__name__)

RES_DIR = Path(__file__).parent.resolve()   # bundled files (prompts, templates)
if getattr(sys, "frozen", False):          # PyInstaller EXE: data lives beside it
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = RES_DIR
# MOJIDORI_HOME moves the data folders (queue/processed/archive) elsewhere.
DATA_DIR = Path(os.environ.get("MOJIDORI_HOME") or BASE_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_DIR = DATA_DIR / "queue"
PROCESSED_DIR = DATA_DIR / "processed"
ARCHIVE_DIR = DATA_DIR / "archive"
DOWNLOAD_DIR = DATA_DIR / "downloads"
SETTINGS_FILE = DATA_DIR / "settings.json"
for _d in (QUEUE_DIR, PROCESSED_DIR, ARCHIVE_DIR, DOWNLOAD_DIR):
    _d.mkdir(exist_ok=True)

# Copy-with-prompt packs: id -> prompt file (allowlist, never user paths)
PROMPTS = {
    "cheat": "prompt_cheat_sheet.md",
    "notes": "prompt_condensed_notes.md",
    "summary": "prompt_descriptive_summary.md",
}

VIEWABLE = {".txt", ".srt", ".vtt", ".md", ".json"}

# ── settings (server-side, so the worker and watcher use them too) ──────────

DEFAULT_SETTINGS = {
    "auto": True,              # start jobs as soon as they arrive
    "provider": "fast",
    "model": "large-v3",
    "language": "English",
    "timestamps": True,
    "segments": True,
    "normalize": True,
    "highpass": True,
    "target_dbfs": None,
    "keep_audio": False,       # keep the cleaned-up WAV next to the transcript
    "device": "auto",
}
_settings_lock = threading.Lock()


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if s["provider"] not in PROVIDERS:
        s["provider"] = DEFAULT_SETTINGS["provider"]
    if s["model"] not in PROVIDERS[s["provider"]]["models"]:
        s["model"] = PROVIDERS[s["provider"]]["default_model"]
    return s


def save_settings(patch: dict) -> dict:
    with _settings_lock:
        s = load_settings()
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                s[k] = v
        if s["provider"] not in PROVIDERS:
            s["provider"] = DEFAULT_SETTINGS["provider"]
        if s["model"] not in PROVIDERS[s["provider"]]["models"]:
            s["model"] = PROVIDERS[s["provider"]]["default_model"]
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
        return s


# ── jobs ────────────────────────────────────────────────────────────────────

ACTIVE = {"waiting", "queued", "running"}


class Job:
    def __init__(self, kind: str, name: str, src: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind            # "file" | "url"
        self.name = name
        self.src = src              # file path or URL
        self.status = "queued"      # waiting|queued|running|done|error|cancelled
        self.stage = "Queued"
        self.pct = None
        self.error = None
        self.result = None
        self.log: list[str] = []
        self.created = time.time()
        self.stage_started = None
        self.finished = None
        self.cancel = threading.Event()
        self._sig = None            # file identity (size, mtime) for the watcher

    def public(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "name": self.name, "src": self.src,
            "status": self.status, "stage": self.stage,
            "pct": None if self.pct is None else round(self.pct, 1),
            "error": self.error, "result": self.result,
            "created": self.created, "stage_started": self.stage_started,
            "finished": self.finished,
        }


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.subscribers: list[queue.Queue] = []
        self._last_push: dict[str, float] = {}
        self._done_since_idle = 0
        threading.Thread(target=self._worker, daemon=True, name="worker").start()
        threading.Thread(target=self._watcher, daemon=True, name="watcher").start()

    # — events —
    def _broadcast(self, event: str, data: dict):
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def push(self, job: Job, force: bool = True):
        now = time.time()
        if not force and now - self._last_push.get(job.id, 0) < 0.3:
            return  # throttle per-segment progress spam
        self._last_push[job.id] = now
        self._broadcast("job", job.public())

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    # — adding —
    def _initial_status(self) -> str:
        return "queued" if load_settings()["auto"] else "waiting"

    def add_file(self, path: Path, status: str = None) -> Job:
        with self.lock:
            for j in self.jobs.values():
                if j.kind == "file" and j.src == str(path) and j.status in ACTIVE:
                    return j
            job = Job("file", path.stem, str(path))
            job.status = status or self._initial_status()
            job.stage = "Waiting for Start" if job.status == "waiting" else "Queued"
            try:
                st = path.stat()
                job._sig = (st.st_size, st.st_mtime)
            except OSError:
                pass
            self.jobs[job.id] = job
        self.push(job)
        self.wake.set()
        return job

    def add_url(self, url: str, title: str = None) -> Job:
        job = Job("url", title or url, url)
        job.stage = "Queued"  # a pasted link is an explicit "do it now"
        with self.lock:
            self.jobs[job.id] = job
        self.push(job)
        self.wake.set()
        return job

    def start_waiting(self) -> int:
        n = 0
        with self.lock:
            for j in self.jobs.values():
                if j.status == "waiting":
                    j.status, j.stage = "queued", "Queued"
                    n += 1
                    self.push(j)
        self.wake.set()
        return n

    def retry(self, job_id: str) -> bool:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j or j.status not in ("error", "cancelled"):
                return False
            if j.kind == "file" and not Path(j.src).exists():
                return False
            j.status, j.stage, j.pct, j.error = "queued", "Queued", None, None
            j.cancel = threading.Event()
            j.log.append("── retry ──")
        self.push(j)
        self.wake.set()
        return True

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j:
                return False
            if j.status in ("waiting", "queued"):
                j.status, j.stage = "cancelled", "Cancelled"
                self.push(j)
                return True
            if j.status == "running":
                j.cancel.set()
                j.stage = "Cancelling…"
                self.push(j)
                return True
        return False

    def remove(self, job_id: str) -> bool:
        """Forget a finished job. A file still sitting in queue/ is moved to
        archive/_removed/ (not deleted) so the watcher doesn't re-add it."""
        with self.lock:
            j = self.jobs.get(job_id)
            if not j or j.status == "running":
                return False
            if j.status in ("waiting", "queued"):
                j.status = "cancelled"
            if j.kind == "file":
                p = Path(j.src)
                if p.exists() and QUEUE_DIR.resolve() in p.resolve().parents:
                    try:
                        archive_original(p, ARCHIVE_DIR, group="_removed")
                    except OSError:
                        pass
            del self.jobs[job_id]
        self._broadcast("removed", {"id": job_id})
        return True

    def clear_finished(self) -> int:
        with self.lock:
            ids = [i for i, j in self.jobs.items() if j.status == "done"]
            for i in ids:
                del self.jobs[i]
        for i in ids:
            self._broadcast("removed", {"id": i})
        return len(ids)

    def snapshot(self) -> list:
        with self.lock:
            return [j.public() for j in self.jobs.values()]

    # — worker —
    def _next(self):
        with self.lock:
            for j in self.jobs.values():
                if j.status == "queued":
                    j.status = "running"
                    return j
        return None

    def _worker(self):
        while True:
            job = self._next()
            if job is None:
                if self._done_since_idle:
                    n, self._done_since_idle = self._done_since_idle, 0
                    maybe_ping_ntfy("Mojidori", f"{n} transcript{'s' * (n != 1)} ready")
                self.wake.wait(2)
                self.wake.clear()
                continue
            self._run(job)

    def _run(self, job: Job):
        s = load_settings()

        def log(line: str):
            job.log.append(line)
            if len(job.log) > 2000:
                del job.log[:500]
            self._broadcast("log", {"id": job.id, "line": line})

        def status(stage: str, pct=None):
            if stage != job.stage:
                job.stage_started = time.time()
                job.stage, job.pct = stage, pct
                self.push(job)
            else:
                job.pct = pct
                self.push(job, force=False)

        job.error = None
        status("Starting")
        downloaded = None
        try:
            src = Path(job.src)
            source = None
            if job.kind == "url":
                status("Downloading", 0.0)
                downloaded, meta = download_audio(
                    job.src, DOWNLOAD_DIR, status_cb=status, progress_cb=log,
                    cancel=job.cancel)
                job.name = meta["title"]
                src, source = downloaded, meta["url"]
            elif not src.exists():
                raise RuntimeError("File is no longer in the queue folder.")

            result = transcribe_file(
                src, PROCESSED_DIR,
                model_name=s["model"], language=LANGUAGES.get(s["language"]),
                normalize=s["normalize"], include_timestamps=s["timestamps"],
                include_segments=s["segments"], device=s["device"],
                provider=s["provider"], target_dbfs=s["target_dbfs"],
                highpass=s["highpass"], keep_audio=s["keep_audio"],
                source=source, title=safe_name(job.name),
                progress_cb=log, status_cb=status, cancel=job.cancel,
            )
            if job.kind == "file":
                try:
                    dest = archive_original(src, ARCHIVE_DIR)
                    log(f"[OK] Original moved to archive/{dest.parent.name}/{dest.name}")
                except OSError as e:
                    log(f"[WARN] Could not archive original: {e}")
            job.result = {"folder": result["folder"], **result["stats"]}
            job.status, job.stage, job.pct = "done", "Done", 100.0
            self._done_since_idle += 1
        except Cancelled:
            job.status, job.stage, job.pct = "cancelled", "Cancelled", None
            log("[WARN] Cancelled")
        except Exception as e:  # noqa: BLE001 — surface every failure to the UI
            job.status, job.stage, job.error = "error", "Failed", str(e)
            log(f"[ERR] {e}")
        finally:
            if downloaded:
                try:
                    downloaded.unlink()
                except OSError:
                    pass
            job.finished = time.time()
            self.push(job)

    # — folder watcher —
    def _watcher(self):
        seen: dict[str, tuple] = {}
        first = True
        while True:
            try:
                current = {}
                for f in get_audio_files(QUEUE_DIR):
                    st = f.stat()
                    current[str(f)] = (st.st_size, st.st_mtime)
                with self.lock:
                    known = {(j.src, j._sig) for j in self.jobs.values() if j.kind == "file"}
                    known_paths = {j.src for j in self.jobs.values()
                                   if j.kind == "file" and j.status in ACTIVE}
                for path, sig in current.items():
                    if path in known_paths or (path, sig) in known:
                        continue
                    if first:
                        # Leftovers from last time: list them, but don't surprise
                        # anyone by starting a 10-lecture batch on launch.
                        self.add_file(Path(path), status="waiting")
                    elif seen.get(path) == sig and sig[0] > 0:   # finished copying
                        self.add_file(Path(path))
                seen = current
                first = False
            except Exception:  # noqa: BLE001 — the watcher must never die
                pass
            time.sleep(2)


jobs = JobManager()


# ── pages + system ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", languages=list(LANGUAGES.keys()))


_system_info: dict = {"gpu": None, "checked": False}


def _probe_system():
    from core import gpu_name
    _system_info["gpu"] = gpu_name()
    _system_info["checked"] = True


threading.Thread(target=_probe_system, daemon=True).start()


@app.route("/api/system")
def api_system():
    try:
        import yt_dlp
        ytdlp = yt_dlp.version.__version__
    except ImportError:
        ytdlp = None
    return jsonify({
        "gpu": _system_info["gpu"], "gpu_checked": _system_info["checked"],
        "ffmpeg": bool(shutil.which("ffmpeg")), "ytdlp": ytdlp,
        "queue_dir": str(QUEUE_DIR), "processed_dir": str(PROCESSED_DIR),
    })


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        before = load_settings()["auto"]
        s = save_settings(request.json or {})
        if s["auto"] and not before:
            jobs.start_waiting()
        return jsonify(s)
    return jsonify(load_settings())


@app.route("/api/providers")
def api_providers():
    """List providers + whether a key is configured (never exposes the key)."""
    out = {}
    for pid, p in PROVIDERS.items():
        out[pid] = {
            "label": p["label"], "needs_key": p["needs_key"],
            "models": p["models"], "default_model": p["default_model"],
            "key_url": p.get("key_url"),
            "key_configured": bool(resolve_api_key(pid)) if p["needs_key"] else True,
        }
    return jsonify(out)


@app.route("/api/key", methods=["POST"])
def api_key_save():
    """Save an API key to .env. Body: {provider, key}."""
    body = request.json or {}
    provider = body.get("provider")
    key = (body.get("key") or "").strip()
    if provider not in PROVIDERS or not PROVIDERS[provider]["needs_key"]:
        return jsonify(error="unknown provider"), 400
    if not key:
        return jsonify(error="empty key"), 400
    env_var = PROVIDERS[provider]["env_var"]
    env_path = BASE_DIR / ".env"
    lines = []
    if env_path.exists():
        lines = [ln for ln in env_path.read_text(encoding="utf-8").splitlines()
                 if not ln.startswith(env_var + "=")]
    lines.append(f"{env_var}={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    os.environ[env_var] = key
    return jsonify(saved=True)


@app.route("/api/key/test", methods=["POST"])
def api_key_test():
    body = request.json or {}
    provider = body.get("provider")
    if provider not in PROVIDERS:
        return jsonify(error="unknown provider"), 400
    key = (body.get("key") or "").strip() or resolve_api_key(provider)
    if not key:
        return jsonify(ok=False, message="No key provided or configured.")
    ok, message = test_api_key(provider, key)
    return jsonify(ok=ok, message=message)


# ── inputs ──────────────────────────────────────────────────────────────────

def _unique(path: Path) -> Path:
    n, out = 1, path
    while out.exists():
        out = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return out


@app.route("/api/upload", methods=["POST"])
def api_upload():
    added = []
    for f in request.files.getlist("files"):
        name = safe_name(Path(f.filename or "").name, limit=180)
        if not name or Path(name).suffix.lower() not in {
                ".mp3", ".mp4", ".wav", ".m4a", ".flac", ".ogg", ".webm", ".avi", ".mov",
                ".mkv", ".wmv", ".aac", ".wma", ".mpeg", ".mpg", ".opus"}:
            continue
        dest = _unique(QUEUE_DIR / name)
        tmp = dest.with_name(dest.name + ".uploading")
        f.save(str(tmp))
        os.replace(tmp, dest)
        added.append(jobs.add_file(dest).public())
    if not added:
        return jsonify(error="No audio or video files in that drop."), 400
    return jsonify(jobs=added)


@app.route("/api/url", methods=["POST"])
def api_url():
    """Body: {text} — any pasted text; every link in it becomes a job.
    Playlists/channels expand into one job per video."""
    text = (request.json or {}).get("text", "")
    urls = find_urls(text)
    if not urls:
        return jsonify(error="That doesn't look like a link."), 400
    added, errors = [], []
    for url in urls:
        try:
            entries = expand_url(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
            continue
        for e in entries:
            added.append(jobs.add_url(e["url"], e["title"]).public())
    if not added:
        return jsonify(error="; ".join(errors) or "Nothing to download."), 400
    return jsonify(jobs=added, errors=errors)


# ── jobs ────────────────────────────────────────────────────────────────────

@app.route("/api/jobs")
def api_jobs():
    return jsonify(jobs.snapshot())


@app.route("/api/jobs/<job_id>/log")
def api_job_log(job_id):
    j = jobs.jobs.get(job_id)
    if not j:
        return jsonify(error="not found"), 404
    return jsonify(j.log)


@app.route("/api/jobs/<job_id>/<action>", methods=["POST"])
def api_job_action(job_id, action):
    fn = {"cancel": jobs.cancel, "retry": jobs.retry, "remove": jobs.remove}.get(action)
    if not fn:
        return jsonify(error="unknown action"), 400
    return jsonify(ok=fn(job_id))


@app.route("/api/start", methods=["POST"])
def api_start():
    return jsonify(started=jobs.start_waiting())


@app.route("/api/jobs/clear", methods=["POST"])
def api_jobs_clear():
    return jsonify(cleared=jobs.clear_finished())


@app.route("/api/events")
def api_events():
    q = jobs.subscribe()

    def gen():
        try:
            yield f"event: snapshot\ndata: {json.dumps(jobs.snapshot())}\n\n"
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            jobs.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── library ─────────────────────────────────────────────────────────────────

def _in_processed(*parts: str) -> Path | None:
    target = PROCESSED_DIR.joinpath(*parts).resolve()
    root = PROCESSED_DIR.resolve()
    if target != root and root not in target.parents:
        return None
    return target


@app.route("/api/library")
def api_library():
    items = []
    for d in PROCESSED_DIR.iterdir():
        if not d.is_dir():
            continue
        files = sorted(f.name for f in d.iterdir() if f.is_file())
        transcript = next((f for f in files if f.endswith("_transcript.txt")), None)
        if not transcript:
            continue
        meta = {}
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        items.append({
            "name": d.name, "transcript": transcript,
            "files": [f for f in files if Path(f).suffix in VIEWABLE | {".wav"}],
            "mtime": (d / transcript).stat().st_mtime,
            "duration_min": meta.get("duration_min"), "words": meta.get("words"),
            "language": meta.get("language"), "engine": meta.get("engine"),
            "source": meta.get("source"), "uncertain": meta.get("uncertain"),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify(items)


@app.route("/api/file/<path:folder>/<path:filename>")
def api_file(folder, filename):
    target = _in_processed(folder, filename)
    if target is None or not target.is_file():
        return jsonify(error="not found"), 404
    if request.args.get("download"):
        return send_from_directory(target.parent, target.name, as_attachment=True)
    if target.suffix not in VIEWABLE:
        return jsonify(error="not viewable"), 400
    return Response(target.read_text(encoding="utf-8"), mimetype="text/plain; charset=utf-8")


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    """Open a lecture folder (or the queue folder) in the file manager."""
    folder = (request.json or {}).get("folder")
    target = QUEUE_DIR if folder == "__queue__" else _in_processed(folder or "")
    if target is None or not target.is_dir():
        return jsonify(error="not found"), 404
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 — local desktop app
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)


@app.route("/api/prompts")
def api_prompts():
    out = [{"id": "none", "title": "Transcript only"}]
    for pid, fname in PROMPTS.items():
        title = pid
        try:
            first = (RES_DIR / fname).read_text(encoding="utf-8").splitlines()[0]
            title = first.lstrip("# ").strip() or pid
        except (OSError, IndexError):
            pass
        out.append({"id": pid, "title": title})
    return jsonify(out)


@app.route("/api/pack")
def api_pack():
    """Prompt template + transcript in one block, ready to paste into an AI."""
    target = _in_processed(request.args.get("folder", ""), request.args.get("file", ""))
    if target is None or target.suffix != ".txt" or not target.is_file():
        return jsonify(error="not found"), 404
    text = target.read_text(encoding="utf-8")
    prompt = request.args.get("prompt", "none")
    if prompt != "none":
        fname = PROMPTS.get(prompt)
        if not fname or not (RES_DIR / fname).is_file():
            return jsonify(error="unknown prompt"), 400
        text = ((RES_DIR / fname).read_text(encoding="utf-8").rstrip()
                + f"\n\n---\n\n# TRANSCRIPT: {target.name}\n\n" + text)
    return Response(text, mimetype="text/plain; charset=utf-8")


# ── main ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    import webbrowser
    parser = argparse.ArgumentParser(description="Mojidori Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Mojidori — {url}\n  Queue:  {QUEUE_DIR}\n  Output: {PROCESSED_DIR}\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
