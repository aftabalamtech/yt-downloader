import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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
    "--add-header", "User-Agent:com.google.android.youtube/17.36.4 (Linux; U; Android 12) gzip",
    "--extractor-args", "youtube:player_client=android,tv_embedded",
]


def sanitize_filename(title: str) -> str:
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.")
    return "".join(c if c in keep else "_" for c in title).strip() or "download"


def fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_format_string(quality: str) -> str:
    if quality == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    height_map = {"720": "720", "480": "480", "360": "360"}
    h = height_map.get(quality)
    if h:
        return (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={h}][ext=mp4]/best"
        )
    return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


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
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    try:
        cmd = [*YT_DLP_BASE, "--dump-json", "-s", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=result.stderr.strip())
        info = json.loads(result.stdout.strip())
        formats = []
        for f in info.get("formats", []):
            height = f.get("height")
            if height and height <= 2160:
                label = f"{height}p"
                if f.get("fps") and f["fps"] > 30:
                    label = f"{height}p{int(f['fps'])}"
                formats.append({
                    "format_id": f["format_id"],
                    "ext": f.get("ext", ""),
                    "height": height,
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
            "duration": fmt_duration(info.get("duration", 0)),
            "duration_seconds": info.get("duration", 0),
            "channel": info.get("channel", info.get("uploader", "")),
            "formats": unique,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/yt/video")
async def yt_video(url: str = Query(...), quality: str = Query("best")):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp_path = tmp.name
    tmp.close()
    out_path = tmp_path.replace(".mp4", ".%(ext)s")

    fmt = get_format_string(quality)

    try:
        cmd = [*YT_DLP_BASE, "-f", fmt, "-o", out_path, "--merge-output-format", "mp4", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise Exception(result.stderr.strip())

        info_cmd = [*YT_DLP_BASE, "--dump-json", "-s", url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        title = "video"
        if info_result.returncode == 0:
            info = json.loads(info_result.stdout.strip())
            title = info.get("title", "video")

        safe = sanitize_filename(title)
        filename = f"{safe}.mp4"
        actual = tmp_path

        def cleanup():
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return FileResponse(
            actual,
            media_type="video/mp4",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{quote(filename)}"'},
            background=BackgroundTask(cleanup),
        )
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/yt/audio")
async def yt_audio(url: str = Query(...)):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp_path = tmp.name
    tmp.close()
    out_path = tmp_path.replace(".mp3", ".%(ext)s")

    try:
        cmd = [
            *YT_DLP_BASE,
            "-f", "bestaudio/best",
            "-o", out_path,
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "192K",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise Exception(result.stderr.strip())

        info_cmd = [*YT_DLP_BASE, "--dump-json", "-s", url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        title = "audio"
        if info_result.returncode == 0:
            info = json.loads(info_result.stdout.strip())
            title = info.get("title", "audio")

        safe = sanitize_filename(title)
        filename = f"{safe}.mp3"
        media_type = "audio/mpeg"
        actual_path = tmp_path
        base = tmp_path.replace(".mp3", "")
        if os.path.exists(base + ".mp3"):
            actual_path = base + ".mp3"
        elif os.path.exists(base + ".m4a"):
            actual_path = base + ".m4a"
            filename = f"{safe}.m4a"
            media_type = "audio/mp4"

        def cleanup():
            try:
                os.unlink(actual_path)
            except Exception:
                pass

        return FileResponse(
            actual_path,
            media_type=media_type,
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{quote(filename)}"'},
            background=BackgroundTask(cleanup),
        )
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
