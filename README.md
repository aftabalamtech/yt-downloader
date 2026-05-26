# YT Downloader

Single-file YouTube Video & Audio Downloader. No database, no auth, no sessions. Stateless.

## Tech Stack

- Python 3.11+
- FastAPI
- yt-dlp
- ffmpeg
- Jinja2
- uvicorn

## Local Run

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# Open: http://localhost:8000
```

## Docker

```bash
docker build -t yt-downloader .
docker run -p 8000:8000 yt-downloader
```

## Deploy on Railway

- Push this repo to GitHub
- Railway → New Project → Deploy from GitHub repo
- Railway auto-detects the Dockerfile
- Public URL assigned automatically

## Deploy on Render

- New Web Service → connect repo
- Use Dockerfile deploy (recommended)
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## API (headless usage — no UI needed)

**Get video info:**
```
GET /yt/info?url=https://youtube.com/watch?v=VIDEO_ID
```

**Download video:**
```
GET /yt/video?url=https://youtube.com/watch?v=VIDEO_ID&quality=720
```

**Download audio (MP3):**
```
GET /yt/audio?url=https://youtube.com/watch?v=VIDEO_ID
```

### Quality options

- `best` — highest available
- `720` — 720p
- `480` — 480p
- `360` — 360p

## Notes

- ffmpeg is required for audio extraction
- Temp files are cleaned up after each download
- All downloads stream directly to the client
