import glob
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask

app = FastAPI(title="YT Downloader", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

YT_DLP_PATH = "/usr/local/bin/yt-dlp"

YT_DLP_BASE = [
    YT_DLP_PATH,
    "--no-check-certificate",
    "--no-warnings",
    "--quiet",
    "--extractor-retries", "3",
    "--socket-timeout", "30",
    "--extractor-args", "youtube:player_client=android",
    "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "--add-header", "Accept-Language:en-US,en;q=0.9",
]


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _clean_url(url: str) -> str:
    url = unquote(url.strip())
    if "shorts/" in url:
        video_id = url.split("shorts/")[1].split("?")[0].split("&")[0]
        return f"https://youtube.com/shorts/{video_id}"
    return url


def _render_html(name: str) -> HTMLResponse:
    path = TEMPLATES_DIR / name
    content = path.read_text(encoding="utf-8")
    return HTMLResponse(content=content)


@app.get("/")
async def index():
    return _render_html("index.html")


@app.get("/dashboard")
@app.head("/dashboard")
async def dashboard():
    return {"status": "ok"}


@app.get("/index.html")
async def index_html():
    return _render_html("index.html")


@app.get("/docs")
async def docs_page():
    return _render_html("docs.html")


@app.get("/docs.html")
async def docs_html():
    return _render_html("docs.html")


@app.get("/yt/info")
async def yt_info(url: str = Query(...)):
    url = _clean_url(url)
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    try:
        cmd = [*YT_DLP_BASE, "--dump-json", "--no-playlist", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return JSONResponse(status_code=400, content={"detail": result.stderr.strip() or "yt-dlp failed"})
        try:
            info = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"detail": f"yt-dlp output: {result.stdout.strip()[:200]}"})
        formats = []
        for f in info.get("formats", []):
            h = f.get("height")
            if h and h <= 2160:
                label = f"{h}p"
                if f.get("fps") and f["fps"] > 30:
                    label = f"{h}p{int(f['fps'])}"
                formats.append({
                    "format_id": f["format_id"],
                    "ext": f.get("ext", ""),
                    "height": h,
                    "label": label,
                    "filesize": f.get("filesize") or f.get("filesize_approx"),
                })
        formats.sort(key=lambda x: (x["height"] or 0), reverse=True)
        seen = set()
        unique = []
        for fm in formats:
            key = fm["label"]
            if key not in seen:
                seen.add(key)
                unique.append(fm)
        return {
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": _fmt_duration(info.get("duration", 0)),
            "duration_seconds": info.get("duration", 0),
            "channel": info.get("channel", info.get("uploader", "")),
            "formats": unique,
        }
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"detail": "yt-dlp timed out"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.get("/yt/video")
async def yt_video(url: str = Query(...), quality: str = Query("best")):
    url = _clean_url(url)
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    try:
        fmt_map = {
            "best": "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
            "720": "bestvideo[height<=720][ext=mp4]+bestaudio/best[ext=mp4]/best",
            "480": "bestvideo[height<=480][ext=mp4]+bestaudio/best[ext=mp4]/best",
            "360": "bestvideo[height<=360][ext=mp4]+bestaudio/best[ext=mp4]/best",
        }
        fmt = fmt_map.get(quality, fmt_map["best"])
        cmd = [*YT_DLP_BASE, "--get-url", "--format", fmt, url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return JSONResponse(status_code=400, content={"detail": result.stderr.strip() or "yt-dlp failed"})
        stream_url = result.stdout.strip()
        if not stream_url:
            return JSONResponse(status_code=400, content={"detail": "No stream URL returned"})
        return RedirectResponse(url=stream_url, status_code=302)
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.get("/yt/audio")
async def yt_audio(url: str = Query(...)):
    url = _clean_url(url)
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    video_id = url.split("shorts/")[-1].split("/")[-1].split("?")[0].split("&")[0]
    if len(video_id) != 11:
        m = __import__("re").search(r"v=([a-zA-Z0-9_-]{11})", url)
        video_id = m.group(1) if m else "audio"
    out_path = f"/tmp/{video_id}.%(ext)s"
    try:
        cmd = [
            *YT_DLP_BASE,
            "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
            "-o", out_path,
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return JSONResponse(status_code=400, content={"detail": result.stderr.strip() or "yt-dlp failed"})
        files = glob.glob(f"/tmp/{video_id}.*")
        if not files:
            return JSONResponse(status_code=500, content={"detail": "Downloaded file not found"})
        dl_path = files[0]
        info_cmd = [*YT_DLP_BASE, "--dump-json", "--no-playlist", url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        title = "audio"
        if info_result.returncode == 0:
            try:
                info = json.loads(info_result.stdout.strip())
                title = info.get("title", "audio")
            except json.JSONDecodeError:
                pass
        keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.")
        safe = "".join(c if c in keep else "_" for c in title).strip() or "download"
        filename = f"{safe}.m4a"

        def cleanup():
            try:
                os.unlink(dl_path)
            except Exception:
                pass

        return FileResponse(
            dl_path,
            media_type="audio/mp4",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            background=BackgroundTask(cleanup),
        )
    except Exception as e:
        try:
            for f in glob.glob(f"/tmp/{video_id}.*"):
                os.unlink(f)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
