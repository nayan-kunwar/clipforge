# ClipForge

Turn a long video — a YouTube VOD or a file you already have — into vertical Shorts of at most 60 seconds. ClipForge finds likely highlight moments, reframes each clip to 9:16, writes titles and a manifest, and can upload the results to your YouTube channel as **Private**.

Gaming stream clips are a typical use case (loud moments, chat timestamps, on-screen stingers), but the pipeline is not tied to a particular game or creator.

## Copyright and terms

**Only clip content you own or have explicit permission to use.** Credit the original creator in the description. Downloading or republishing other people's YouTube videos without authorization can violate copyright law and [YouTube's Terms of Service](https://www.youtube.com/t/terms). This tool does not give you those rights.

## Architecture

```text
CLIPFORGE – ARCHITECTURE (v0)

Input
  - YouTube live/VOD URL or a local video file (+ optional user timestamps)
  - Rules: Shorts max 60 s, 9:16 vertical

Folder layout
  work/<video-id>/
    video360.mp4, audio.m4a, metadata.json, comments.json
    transcript.json      # faster-whisper output (optional)
    waveform.json        # compact min/max peaks for the web timeline
    features.npz         # cached per-second audio features
    analysis.json        # all raw candidates + selected moments
    src1080/             # 1080p source segments for chosen moments
    shorts/              # final rendered Shorts
    thumbs/              # thumbnails
    CLIPS_MANIFEST.json  # start time, length, title, tags per clip

1. INGEST (yt-dlp, or a local file)
   - Fetch metadata + comments
   - Download 360p video + audio for analysis
     (tv_embedded player client as a fallback)
   - Later: download 1080p only for selected segments
     (yt-dlp --download-sections)

2. MOMENT DETECTION
   Signals:
     a. Comment timestamps (viewers marking moments)
     b. Audio loudness peaks (ffmpeg / numpy RMS)
     c. Transcript cues via faster-whisper (keywords, e.g. wasted, countdown)
     d. User-provided timestamps (highest priority)
   -> Merge + rank candidates -> pick top N

3. RENDER (ffmpeg)
   - Cut up to 59.95 s per clip from 1080p source
     (falls back to the analysis file)
   - Canvas 1080x1920:
       background = same clip, scaled + blurred
       foreground = gameplay scaled to width
         (keeps a side facecam in frame)
   - Title text overlay for the first 3.5 s
   - Encode H.264 + AAC

4. PACKAGE
   - Write CLIPS_MANIFEST.json
     (start, duration, title, description, tags)
   - Generate thumbnails

5. PUBLISH (optional, separate command)
   - YouTube Data API upload (youtube.upload scope only)
   - Default privacy: Private
   - Quota: ~6 uploads/day on a default API project
   - Requires your own OAuth client_secret.json (never committed)

Not included (v0)
  - No queue, database, or web dashboard
  - Heuristic clip ranking (optional transcript cues; no hosted LLM)
  - One video at a time

Tools
  yt-dlp | ffmpeg | faster-whisper | Python (analysis) | YouTube Data API
```

## Setup

You need Python 3.10+, [ffmpeg](https://ffmpeg.org/) (and ffprobe), and a bold sans-serif font. yt-dlp is installed with the package.

```bash
# Debian / Ubuntu
sudo apt-get install -y ffmpeg fonts-liberation python3-pip python3-venv

# macOS
brew install ffmpeg

# Windows
winget install Gyan.FFmpeg
winget install yt-dlp
```

Then, from this repository:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

# Optional extras
pip install -e ".[transcribe]"   # faster-whisper for transcript cues
pip install -e ".[upload]"       # YouTube Data API upload
pip install -e ".[all]"
```

Or with Docker (core tools only; pass `--build-arg EXTRAS=[all]` for extras):

```bash
docker build -t clipforge .
docker run --rm -v "$PWD:/data" clipforge --help
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

The media tests require `ffmpeg` and `ffprobe` on `PATH`; they are skipped otherwise.

## Usage

Full pipeline on a YouTube URL, at most 8 clips of 60 seconds, with your own timestamps taking priority:

```bash
clipforge run "https://www.youtube.com/watch?v=YOUR_VIDEO_ID" \
  --max-len 60 --count 8 --timestamps 00:04:14,01:32:16
```

Same pipeline on a file you already have:

```bash
clipforge run ./my-stream.mp4 --max-len 60 --count 8 --skip-transcript
```

Useful flags for `run`:

| Flag | Meaning |
| --- | --- |
| `--out DIR` | Work root (default `./work`) |
| `--skip-transcript` | Don't run faster-whisper |
| `--skip-1080` | Render from the 360p analysis file |
| `--no-comments` | Don't fetch YouTube comments |
| `--keywords wasted,crash` | Custom transcript keywords |
| `--fps 60` | Output frame rate (default 30; use 60 for high-fps gameplay) |
| `--font /path/to.ttf` | Title overlay font |

You can also run the stages separately:

```bash
clipforge ingest "https://www.youtube.com/watch?v=YOUR_VIDEO_ID" --out work
clipforge detect --work-dir work/YOUR_VIDEO_ID --count 8 --timestamps 00:04:14
clipforge render --work-dir work/YOUR_VIDEO_ID --fps 60
```

Cut a single local file without running discovery (handy for a test render):

```bash
clipforge render --input ./source.mp4 --start 0 --duration 5 \
  --title "TEST CLIP" --output-dir ./out
```

### YouTube upload (optional)

Uploads use the **minimum** OAuth scope (`youtube.upload`) and default to **Private**. Create a Desktop OAuth client in [Google Cloud](https://console.cloud.google.com/), enable the YouTube Data API, and download `client_secret.json`. Do not commit that file or the generated `token.json`.

```bash
clipforge upload --work-dir work/YOUR_VIDEO_ID \
  --client-secret /path/to/client_secret.json \
  --privacy private
```

Notes:

- The default API quota is 10,000 units/day. A video insert costs about 1,600 units, so you get roughly **6 uploads per day**.
- Unverified projects are often limited to Private until Google reviews the app. Change visibility later in YouTube Studio if you need Unlisted or Public.
- Run the upload command on a machine where you can complete the Google sign-in in a browser.

## Project layout

```text
src/clipforge/
  cli.py        Command-line entry (`clipforge` / `python -m clipforge`)
  pipeline.py   Shared ingest/analyze/render orchestration + job context
  ingest.py     yt-dlp metadata, comments, 360p + sectioned 1080p
  detect.py     Comments, RMS peaks, optional transcript cues, merge/rank
  audio_features.py  Cached audio features + timeline waveform
  render.py     ffmpeg 1080x1920 Shorts + thumbnails
  manifest.py   CLIPS_MANIFEST.json
  upload.py     YouTube Data API stub (OAuth, Private by default)
```
