import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.requests import Request

app = FastAPI(title="YT Downloader", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

YT_DLP_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
}


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


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "active": "home"})


@app.get("/index.html")
async def index_html(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "active": "home"})


@app.get("/docs")
async def docs_page():
    from fastapi.responses import HTMLResponse
    path = BASE_DIR / "templates" / "docs.html"
    content = path.read_text(encoding="utf-8")
    return HTMLResponse(content=content)


@app.get("/docs.html")
async def docs_html():
    from fastapi.responses import HTMLResponse
    path = BASE_DIR / "templates" / "docs.html"
    content = path.read_text(encoding="utf-8")
    return HTMLResponse(content=content)


@app.get("/yt/info")
async def yt_info(url: str = Query(...)):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
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
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/yt/video")
async def yt_video(url: str = Query(...), quality: str = Query("best")):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp_path = tmp.name
    tmp.close()

    fmt = get_format_string(quality)
    opts = {
        **YT_DLP_OPTS,
        "format": fmt,
        "outtmpl": tmp_path,
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
        safe = sanitize_filename(title)
        filename = f"{safe}.mp4"

        def cleanup():
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return FileResponse(
            tmp_path,
            media_type="video/mp4",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{quote(filename)}"'},
            background=BackgroundTask(cleanup),
        )
    except yt_dlp.utils.DownloadError as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {str(e)}")
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

    opts = {
        **YT_DLP_OPTS,
        "format": "bestaudio/best",
        "outtmpl": tmp_path.replace(".mp3", ""),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "audio")
        safe = sanitize_filename(title)
        filename = f"{safe}.mp3"

        actual_path = tmp_path
        mp3_path = tmp_path.replace(".mp3", ".mp3")
        if os.path.exists(mp3_path):
            actual_path = mp3_path
        elif os.path.exists(tmp_path):
            actual_path = tmp_path
        else:
            base = tmp_path.replace(".mp3", "")
            if os.path.exists(base + ".mp3"):
                actual_path = base + ".mp3"
            elif os.path.exists(base + ".m4a"):
                actual_path = base + ".m4a"
                filename = f"{safe}.m4a"

        def cleanup():
            try:
                os.unlink(actual_path)
            except Exception:
                pass

        media_type = "audio/mpeg"
        if actual_path.endswith(".m4a"):
            media_type = "audio/mp4"

        return FileResponse(
            actual_path,
            media_type=media_type,
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{quote(filename)}"'},
            background=BackgroundTask(cleanup),
        )
    except yt_dlp.utils.DownloadError as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {str(e)}")
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
