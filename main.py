import glob
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
    "--extractor-args", "youtube:player_client=android,ios",
    "--ffmpeg-location", "/usr/bin/ffmpeg",
    "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "--add-header", "Accept-Language:en-US,en;q=0.9",
]


def _cleanup_old_files():
    for pattern in ["/tmp/*.mp4", "/tmp/*.mp3", "/tmp/*.m4a", "/tmp/*.webm"]:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except Exception:
                pass


def _extract_video_id(url: str) -> str:
    if "shorts/" in url:
        return url.split("shorts/")[1].split("?")[0].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("&")[0]
    m = __import__("re").search(r"v=([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return "video"


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


def _file_headers(filename: str) -> dict:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
    }


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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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
    video_id = _extract_video_id(url)
    height_map = {"best": 1080, "720": 720, "480": 480, "360": 360}
    height = height_map.get(quality, 1080)
    fmt = (
        f"bestvideo[height<={height}][ext=mp4]"
        f"+bestaudio[ext=m4a]"
        f"/bestvideo[height<={height}]+bestaudio"
        f"/best[height<={height}]/best"
    )
    out_path = f"/tmp/{video_id}.mp4"
    _cleanup_old_files()
    try:
        cmd = [
            *YT_DLP_BASE,
            "-f", fmt,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", out_path,
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return JSONResponse(status_code=400, content={"detail": result.stderr.strip() or "yt-dlp failed"})
        files = glob.glob(f"/tmp/{video_id}*")
        if not files:
            return JSONResponse(status_code=500, content={"detail": "Downloaded file not found"})
        dl_path = files[0]
        info_cmd = [*YT_DLP_BASE, "--dump-json", "--no-playlist", url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=300)
        title = "video"
        if info_result.returncode == 0:
            try:
                info = json.loads(info_result.stdout.strip())
                title = info.get("title", "video")
            except json.JSONDecodeError:
                pass
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or "download"
        filename = f"{safe}.mp4"

        def cleanup():
            try:
                os.remove(dl_path)
            except Exception:
                pass

        return FileResponse(
            dl_path,
            media_type="video/mp4",
            filename=filename,
            headers=_file_headers(filename),
            background=BackgroundTask(cleanup),
        )
    except Exception as e:
        try:
            for f in glob.glob(f"/tmp/{video_id}*"):
                os.remove(f)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.get("/yt/audio")
async def yt_audio(url: str = Query(...)):
    url = _clean_url(url)
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    video_id = _extract_video_id(url)
    out_path = f"/tmp/{video_id}.%(ext)s"
    _cleanup_old_files()
    try:
        cmd = [
            *YT_DLP_BASE,
            "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
            "--no-playlist",
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "-o", out_path,
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return JSONResponse(status_code=400, content={"detail": result.stderr.strip() or "yt-dlp failed"})
        files = glob.glob(f"/tmp/{video_id}*")
        if not files:
            return JSONResponse(status_code=500, content={"detail": "Downloaded file not found"})
        dl_path = files[0]
        info_cmd = [*YT_DLP_BASE, "--dump-json", "--no-playlist", url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=300)
        title = "audio"
        if info_result.returncode == 0:
            try:
                info = json.loads(info_result.stdout.strip())
                title = info.get("title", "audio")
            except json.JSONDecodeError:
                pass
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or "download"
        filename = f"{safe}.mp3"

        def cleanup():
            try:
                os.remove(dl_path)
            except Exception:
                pass

        return FileResponse(
            dl_path,
            media_type="audio/mpeg",
            filename=filename,
            headers=_file_headers(filename),
            background=BackgroundTask(cleanup),
        )
    except Exception as e:
        try:
            for f in glob.glob(f"/tmp/{video_id}*"):
                os.remove(f)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
