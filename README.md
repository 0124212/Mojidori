# Mojidori (文字取り) — catch every word

Whisper-powered lecture transcription with a modern web UI. Works on **Windows, macOS, and Linux**.

Drop audio/video files → get clean, AI-ready transcripts.

## Install

### Windows — the easy way (no Python, no setup)

1. Go to **Releases** (right side of the GitHub page) and download `Mojidori-win64.zip`
2. Unzip it anywhere
3. Double-click **`Mojidori.exe`**
4. Your browser opens with the app. Done.

First transcription downloads the speech model once (~3 GB, automatic) — after that it's instant.
Tip: paste a free [Groq key](https://console.groq.com/keys) in Engine settings to skip the download entirely and transcribe ~10× faster in the cloud.

### Windows — from source (double-click)

Double-click **`START.bat`**. It installs Python/FFmpeg itself if they're missing, then opens the app.

### macOS / Linux — one command

```bash
git clone https://github.com/0124212/Mojidori.git
cd Mojidori
python3 start.py
```

Opens at **http://localhost:8080**. Python packages auto-install on first run; you only need FFmpeg (`brew install ffmpeg` / `sudo apt install ffmpeg`).

## Usage

### Web UI (recommended)

```bash
python start.py              # Default port 8080
python start.py --port 9000  # Custom port
python start.py --no-browser # Don't auto-open browser
```

1. **Give it something** — any of these works, and with **Auto-start** on (default) it starts right away:
   - drop recordings anywhere on the page (or click to browse)
   - paste a **YouTube link** into the box — or just press **Ctrl+V anywhere on the page**. Playlists become one job per video. Most other video sites work too (anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports).
   - copy files into the `queue/` folder — they're picked up once they finish copying
2. **Watch the progress bar** — each job shows its stage, % done, and time left. Cancel or retry any time; "Log" shows exactly what the engine is doing.
3. **Read, search, copy** — click a transcript to read it in the app, search inside it, copy it (optionally with a study prompt attached, ready for your AI), or download the `.txt` / `.srt` / `.vtt`. For YouTube transcripts, the timestamps link back to that moment in the video.

Good to know:
- **Quality** — *Accurate* (large-v3, default) or *Quick* (large-v3-turbo, ~1.5× faster, a few more slips). More engines — including Groq/OpenAI cloud — are under ⚙ Settings.
- **Nothing runs twice** — one job at a time, in order. Finished originals move to `archive/<ClassCode>/`.
- **Files already in `queue/` when the app starts** are listed but wait for you to press Start, so launching never kicks off a surprise batch.
- **📱 Done ping** — set `NTFY_TOPIC` in `.env` and your phone buzzes when the queue empties.
- **Settings are saved** in `settings.json` and apply to everything, including files dropped into `queue/`.
- **Different data folder** — set `MOJIDORI_HOME` to keep queue/processed/archive somewhere else.

### CLI (legacy)

```bash
python start.py --cli
# or directly:
python transcribe_lecture.py -m large-v3 -l en
```

## Features

- **Free, always** — local engines cost $0 forever; Groq cloud tier is free too. Only OpenAI bills.
- **GPU by default** — faster-whisper on CUDA: a 93-min lecture takes ~5 min on an RTX 3060. `START.bat` installs the CUDA build of PyTorch automatically when it finds an NVIDIA GPU.
- **Accuracy settings** — beam search (5 beams) on every engine, fallback re-decoding at higher temperatures, and filters that drop repeated or low-confidence (hallucinated) segments.
- **YouTube & links** — paste a video or playlist link; audio-only download, nothing re-encoded
- **Clean transcripts** — artifact cleanup, verify-flags on shaky segments, word counts, reading time
- **Organized outputs** — `.txt` transcript + `.srt`/`.vtt` subtitles + raw JSON + `meta.json`, per lecture
- **Audio pipeline** — FFmpeg high-pass + mono + 16 kHz, 2-pass gain, limiter, verified by re-measure
- **Batch processing** — whole queue in one run, originals auto-archived by class code
- **10 languages** — auto-detect or specify manually

## Model Sizes

| Model | Speed | Quality | Use Case |
|-------|-------|---------|----------|
| `tiny` | Fastest | Low | Quick drafts |
| `base` | Fast | Low-Med | |
| `small` | Medium | Medium | Fast preset |
| `medium` | Slow | Good | |
| `large-v3` | Slowest | Best | Recommended |

## Project Structure

```
Mojidori/
├── start.py              # Universal launcher
├── app.py                # Flask web UI server
├── core.py               # Transcription engine (importable)
├── transcribe_lecture.py # CLI mode (legacy)
├── transcribe_gui.py     # tkinter GUI (legacy)
├── templates/
│   └── index.html        # Web UI
├── queue/                # Drop files here
├── processed/            # Output transcripts
├── prompt_*.md           # AI prompt templates for post-processing
└── requirements.txt
```

## Output

Each lecture gets a folder under `processed/`:

```
processed/
└── C188 Lecture 1/
    ├── C188 Lecture 1_transcript.txt      # Formatted transcript
    ├── C188 Lecture 1_transcript_raw.json # Full Whisper output
    ├── C188 Lecture 1.srt / .vtt          # Subtitles
    └── meta.json                          # Duration, words, engine, source link
```

## Post-Processing with AI

The `prompt_*.md` files are templates for turning transcripts into study materials:

- **prompt_descriptive_summary.md** — Full detailed notes (open-book exams)
- **prompt_condensed_notes.md** — High-yield study notes
- **prompt_cheat_sheet.md** — Ultra-dense one-page reference

Paste a transcript into your AI tool with one of these prompts for instant study materials.

## Supported Formats

MP3, MP4, WAV, M4A, FLAC, OGG, WebM, AVI, MOV, MKV, AAC, WMA, MPEG — anything FFmpeg can decode.

## License

MIT — see [LICENSE](LICENSE). Free forever, open source.
