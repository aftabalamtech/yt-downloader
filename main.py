import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

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

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://piped-api.garudalinux.org",
    "https://api.piped.projectsegfau.lt",
    "https://pipedapi.bocchitherock.it",
]


def _extract_video_id(url: str) -> str | None:
    m = re.search(r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return qs.get("v", [None])[0]


def _piped_json(path: str) -> dict:
    last_err = ""
    for base in PIPED_INSTANCES:
        try:
            req = urllib.request.Request(f"{base}{path}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    if isinstance(data, dict) and "error" not in data:
                        return data
                    last_err = data.get("error", "unknown error")
                else:
                    last_err = f"HTTP {resp.status}"
        except Exception as e:
            last_err = str(e)
    raise Exception(f"All Piped instances failed: {last_err}")


def _pick_video_stream(streams: list, quality: str) -> tuple[str, str] | None:
    if quality == "best":
        for s in streams:
            if not s.get("videoOnly", True) and s.get("url"):
                return s["url"], s.get("mimeType", "video/mp4")
        for s in streams:
            if s.get("url"):
                return s["url"], s.get("mimeType", "video/mp4")
    target = int(quality)
    best = None
    for s in streams:
        h = 0
        q = s.get("quality", "")
        m = re.search(r"(\d+)", q)
        if m:
            h = int(m.group(1))
        if h == target or (best is None and not s.get("videoOnly", True)):
            if h == target:
                best = s
    if best and best.get("url"):
        return best["url"], best.get("mimeType", "video/mp4")
    for s in streams:
        if s.get("url"):
            return s["url"], s.get("mimeType", "video/mp4")
    return None


def _pick_audio_stream(streams: list) -> tuple[str, str] | None:
    best = None
    best_bitrate = 0
    for s in streams:
        bitrate = s.get("bitrate", 0) or 0
        if bitrate > best_bitrate and s.get("url"):
            best_bitrate = bitrate
            best = s
    if best and best.get("url"):
        return best["url"], best.get("mimeType", "audio/mp4")
    return None


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _sanitize_filename(name: str) -> str:
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.")
    return "".join(c if c in keep else "_" for c in name).strip() or "download"


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


@app.get("/yt/debug")
async def yt_debug():
    results = []
    for inst in PIPED_INSTANCES:
        try:
            req = urllib.request.Request(f"{inst}/health", headers={"User-Agent": "Mozilla/5.0"}, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                results.append({"instance": inst, "status": resp.status})
        except Exception as e:
            results.append({"instance": inst, "error": str(e)})
    return {"piped_instances": results}


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
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    video_id = _extract_video_id(url)
    if not video_id:
        return JSONResponse(status_code=400, content={"detail": "Could not extract video ID"})
    try:
        data = _piped_json(f"/streams/{video_id}")
        formats = []
        seen_labels = set()
        for s in data.get("videoStreams", []):
            q = s.get("quality", "")
            m = re.search(r"(\d+)", q)
            if m:
                h = int(m.group(1))
                label = f"{h}p"
                if label not in seen_labels:
                    seen_labels.add(label)
                    formats.append({
                        "format_id": s.get("format", ""),
                        "ext": s.get("mimeType", "").split("/")[-1] or "mp4",
                        "height": h,
                        "label": label,
                        "filesize": None,
                    })
        formats.sort(key=lambda x: (x["height"] or 0), reverse=True)
        return {
            "title": data.get("title", ""),
            "thumbnail": data.get("thumbnailUrl", ""),
            "duration": _fmt_duration(data.get("duration", 0)),
            "duration_seconds": data.get("duration", 0),
            "channel": data.get("uploader", ""),
            "formats": formats,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


def _stream_file(stream_url: str, mime_type: str, filename: str) -> StreamingResponse:
    async def generate():
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.youtube.com",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            async with client.stream("GET", stream_url) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        generate(),
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/yt/video")
async def yt_video(url: str = Query(...), quality: str = Query("best")):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    video_id = _extract_video_id(url)
    if not video_id:
        return JSONResponse(status_code=400, content={"detail": "Could not extract video ID"})
    try:
        data = _piped_json(f"/streams/{video_id}")
        title = data.get("title", "video")
        picked = _pick_video_stream(data.get("videoStreams", []), quality)
        if not picked:
            return JSONResponse(status_code=400, content={"detail": "No suitable stream found"})
        stream_url, mime_type = picked
        filename = _sanitize_filename(title) + ".mp4"
        return _stream_file(stream_url, mime_type, filename)
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.get("/yt/audio")
async def yt_audio(url: str = Query(...)):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JSONResponse(status_code=400, content={"detail": "Invalid YouTube URL"})
    video_id = _extract_video_id(url)
    if not video_id:
        return JSONResponse(status_code=400, content={"detail": "Could not extract video ID"})
    try:
        data = _piped_json(f"/streams/{video_id}")
        title = data.get("title", "audio")
        picked = _pick_audio_stream(data.get("audioStreams", []))
        if not picked:
            return JSONResponse(status_code=400, content={"detail": "No audio stream found"})
        stream_url, mime_type = picked
        ext = "mp4" if "mp4" in mime_type else "webm"
        filename = _sanitize_filename(title) + f".{ext}"
        return _stream_file(stream_url, mime_type, filename)
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
