from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp
import tempfile
from pathlib import Path
import uuid
import shutil
import time
import zipfile
import re
import subprocess
import sys
import json
import html as html_lib
import asyncio
from collections import defaultdict, deque
from urllib.parse import urlparse, urljoin
import requests

app = FastAPI(title="InstaGrab API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "social-downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_TTL_SECONDS = 60 * 60
MAX_FILE_BYTES = 250 * 1024 * 1024
MAX_TOTAL_JOB_BYTES = 500 * 1024 * 1024
MAX_CAROUSEL_ITEMS = 20
MAX_REQUEST_SECONDS = 120
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 10
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


class DownloadRequest(BaseModel):
    url: HttpUrl


def validate_instagram(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            return False
        return host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am" or host.endswith(".instagr.am")
    except ValueError:
        return False


def rate_limit_key(request: Request) -> str:
    # Use the direct client address. When deploying behind a trusted reverse
    # proxy, configure the proxy/uvicorn correctly rather than trusting an
    # arbitrary X-Forwarded-For header from the public internet.
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> None:
    now = time.monotonic()
    key = rate_limit_key(request)
    bucket = _RATE_BUCKETS[key]
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            429,
            {
                "code": "RATE_LIMITED",
                "title": "Too many requests",
                "message": "Please wait a little before trying another download.",
            },
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )
    bucket.append(now)


def enforce_job_limits(job_dir: Path, started_at: float) -> None:
    if time.monotonic() - started_at > MAX_REQUEST_SECONDS:
        raise TimeoutError("The media request took too long")
    total = 0
    for p in job_dir.iterdir():
        if not p.is_file() or p.name.endswith((".part", ".ytdl")):
            continue
        try:
            total += p.stat().st_size
        except OSError:
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("A media file exceeded the 250 MB safety limit")
        if total > MAX_TOTAL_JOB_BYTES:
            raise ValueError("The download exceeded the 500 MB total safety limit")


def cleanup_expired_jobs() -> None:
    now = time.time()
    try:
        entries = list(DOWNLOAD_DIR.iterdir())
    except OSError:
        return
    for job_dir in entries:
        if not job_dir.is_dir():
            continue
        try:
            if now - job_dir.stat().st_mtime > JOB_TTL_SECONDS:
                shutil.rmtree(job_dir, ignore_errors=True)
        except OSError:
            pass


def cleanup_rate_buckets() -> None:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    stale = []
    for key, bucket in list(_RATE_BUCKETS.items()):
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            stale.append(key)
    for key in stale:
        _RATE_BUCKETS.pop(key, None)


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(10 * 60)
        cleanup_expired_jobs()
        cleanup_rate_buckets()


@app.on_event("startup")
async def start_cleanup_loop() -> None:
    app.state.cleanup_task = asyncio.create_task(cleanup_loop())


@app.on_event("shutdown")
async def stop_cleanup_loop() -> None:
    task = getattr(app.state, "cleanup_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def safe_job_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{32}", value))


def safe_filename(value: str) -> bool:
    return value == Path(value).name and value not in {"", ".", ".."}


def media_kind(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in {".mp4", ".webm", ".mov", ".m4v", ".mkv"}:
        return "video"
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return "photo"
    return "file"


def media_files(job_dir: Path) -> list[Path]:
    return sorted(
        p for p in job_dir.iterdir()
        if p.is_file()
        and p.stat().st_size > 0
        and not p.name.endswith((".part", ".ytdl"))
        and p.suffix.lower() not in {".json", ".txt"}
        and p.name != "instagram-media.zip"
    )


def run_yt_dlp(url: str, job_dir: Path) -> tuple[list[Path], dict | None, str]:
    output = job_dir / "media_%(autonumber)03d.%(ext)s"
    opts = {
        "outtmpl": str(output),
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 15,
        "retries": 2,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = media_files(job_dir)
    if not files:
        raise RuntimeError("yt-dlp did not create any media files")
    return files, info if isinstance(info, dict) else None, ""


IG_APP_ID = "936619743392459"
IG_CRAWLER_UA = "Googlebot/2.1 (+http://www.google.com/bot.html)"
IG_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
# Instagram rotates these internal GraphQL document IDs. Keep the current
# resolver as a fallback; the crawler-page strategy below does not depend on it.
IG_GRAPHQL_DOC_IDS = [
    "25531498899829322",  # PolarisPostActionLoadPostQueryQuery
    "27128499623469141",  # PolarisPostRootQuery (newer API shape)
]


def extract_balanced_json(text: str, start: int) -> str | None:
    """Return a balanced JSON object beginning at start, respecting strings."""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def pick_best_image(item: dict) -> str | None:
    candidates = ((item.get("image_versions2") or {}).get("candidates") or [])
    if candidates:
        # Instagram normally puts the largest candidate first, but sorting by
        # pixel area makes this resilient to ordering changes.
        valid = [c for c in candidates if isinstance(c, dict) and c.get("url")]
        if valid:
            valid.sort(key=lambda c: (c.get("width") or 0) * (c.get("height") or 0), reverse=True)
            return valid[0]["url"]
    return item.get("display_uri") or item.get("display_url")


def pick_best_video(item: dict) -> str | None:
    versions = item.get("video_versions") or []
    valid = [v for v in versions if isinstance(v, dict) and v.get("url")]
    if valid:
        valid.sort(key=lambda v: (v.get("width") or 0) * (v.get("height") or 0), reverse=True)
        return valid[0]["url"]
    return item.get("video_url")


def normalize_xig(node: dict) -> dict | None:
    children = node.get("carousel_media") or []
    items = children if children else [node]
    normalized = []
    for item in items:
        video = pick_best_video(item)
        image = pick_best_image(item)
        if video or image:
            normalized.append({"isVideo": bool(video), "videoUrl": video, "imageUrl": image})
    if not normalized:
        return None
    user = node.get("user") or node.get("owner") or {}
    return {
        "isVideo": normalized[0]["isVideo"],
        "videoUrl": normalized[0]["videoUrl"],
        "imageUrl": normalized[0]["imageUrl"],
        "username": user.get("username"),
        "fullName": user.get("full_name"),
        "caption": ((node.get("caption") or {}).get("text") if isinstance(node.get("caption"), dict) else None),
        "takenAt": node.get("taken_at"),
        "width": items[0].get("original_width"),
        "height": items[0].get("original_height"),
        "children": normalized if len(normalized) > 1 else None,
    }


def normalize_gql(node: dict) -> dict | None:
    edges = ((node.get("edge_sidecar_to_children") or {}).get("edges") or [])
    children = [e.get("node") for e in edges if isinstance(e, dict) and isinstance(e.get("node"), dict)]
    items = children if children else [node]
    normalized = []
    for item in items:
        video = item.get("video_url") if item.get("is_video") else None
        image = item.get("display_url") or item.get("thumbnail_src")
        if video or image:
            normalized.append({"isVideo": bool(video), "videoUrl": video, "imageUrl": image})
    if not normalized:
        return None
    owner = node.get("owner") or {}
    caption_edges = ((node.get("edge_media_to_caption") or {}).get("edges") or [])
    caption = caption_edges[0].get("node", {}).get("text") if caption_edges else None
    return {
        "isVideo": normalized[0]["isVideo"],
        "videoUrl": normalized[0]["videoUrl"],
        "imageUrl": normalized[0]["imageUrl"],
        "username": owner.get("username"),
        "fullName": owner.get("full_name"),
        "caption": caption,
        "takenAt": node.get("taken_at_timestamp"),
        "width": (items[0].get("dimensions") or {}).get("width"),
        "height": (items[0].get("dimensions") or {}).get("height"),
        "children": normalized if len(normalized) > 1 else None,
    }


def parse_og_image(html: str) -> str | None:
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.I)
        if m:
            return html_lib.unescape(m.group(1))
    return None


def fetch_instagram_page(shortcode: str) -> tuple[dict | None, str | None]:
    url = f"https://www.instagram.com/p/{shortcode}/"
    headers = {"User-Agent": IG_CRAWLER_UA, "Accept-Language": "en-US,en;q=0.9"}
    response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    if response.status_code >= 400:
        return None, None
    text = response.text
    media = None
    marker = '"xig_polaris_media":'
    idx = text.find(marker)
    if idx >= 0:
        start = text.find("{", idx)
        raw = extract_balanced_json(text, start)
        if raw:
            try:
                obj = json.loads(raw)
                node = obj.get("if_not_gated_logged_out") or (obj if obj.get("media_type") else None)
                if isinstance(node, dict):
                    media = normalize_xig(node)
            except (ValueError, TypeError):
                pass
    return media, parse_og_image(text)


def fetch_instagram_graphql(shortcode: str) -> dict | None:
    headers = {
        "User-Agent": IG_BROWSER_UA,
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-IG-App-ID": IG_APP_ID,
        "X-ASBD-ID": "129477",
        "Origin": "https://www.instagram.com",
        "Referer": f"https://www.instagram.com/p/{shortcode}/",
    }
    for doc_id in IG_GRAPHQL_DOC_IDS:
        if doc_id == "27128499623469141":
            variables = {
                "shortcode": shortcode,
                "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
            }
            data = {
                "doc_id": doc_id,
                "variables": json.dumps(variables, separators=(",", ":")),
            }
        else:
            variables = {
                "shortcode": shortcode,
                "fetch_comment_count": 40,
                "parent_comment_count": 24,
                "child_comment_count": 3,
                "fetch_like_count": 10,
                "fetch_tagged_user_count": None,
                "fetch_preview_comment_count": 2,
                "has_threaded_comments": True,
                "hoisted_comment_id": None,
                "hoisted_reply_id": None,
            }
            data = {
                "av": "0", "__d": "www", "__user": "0", "__a": "1", "__comet_req": "7",
                "lsd": "AVqbxe3J_YA",
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "PolarisPostActionLoadPostQueryQuery",
                "variables": json.dumps(variables, separators=(",", ":")),
                "server_timestamps": "true",
                "doc_id": doc_id,
            }
        try:
            response = requests.post("https://www.instagram.com/graphql/query/", headers=headers,
                                     cookies={"csrftoken": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
                                     data=data, timeout=20)
            if response.status_code >= 400:
                continue
            payload = response.json()
            root = payload.get("data") or {}
            node = root.get("xdt_api__v1__media__shortcode__web_info", {}).get("items", [None])[0]
            if node:
                media = normalize_xig(node)
                if media:
                    return media
            node = root.get("xdt_shortcode_media") or root.get("shortcode_media")
            if node:
                media = normalize_gql(node)
                if media:
                    return media
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
            continue
    return None


def fetch_instagram_embed(shortcode: str) -> dict | None:
    url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    try:
        response = requests.get(url, headers={"User-Agent": IG_BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=20)
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    text = response.text
    marker = '\\"gql_data\\"'
    idx = text.find(marker)
    if idx >= 0:
        # Try to recover the JSON object following gql_data.
        start = text.find("{", idx)
        raw = extract_balanced_json(text.replace('\\"', '"'), start)
        if raw:
            try:
                obj = json.loads(raw)
                node = obj.get("shortcode_media") or obj.get("xdt_shortcode_media")
                if node:
                    return normalize_gql(node)
            except (ValueError, TypeError):
                pass
    m = re.search(r'class=["\']EmbeddedMediaImage["\'][^>]*src=["\']([^"\']+)', text, re.I)
    if m:
        return {"isVideo": False, "videoUrl": None, "imageUrl": html_lib.unescape(m.group(1)),
                "username": None, "fullName": None, "caption": None, "children": None}
    return None


def instagram_shortcode(url: str) -> str | None:
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", url, re.I)
    return m.group(1) if m else None


def resolve_instagram_direct(url: str) -> dict | None:
    shortcode = instagram_shortcode(url)
    if not shortcode:
        return None
    page_media, og_image = fetch_instagram_page(shortcode)
    if page_media:
        return page_media
    gql = fetch_instagram_graphql(shortcode)
    if gql:
        return gql
    embed = fetch_instagram_embed(shortcode)
    if embed:
        return embed
    if og_image:
        return {"isVideo": False, "videoUrl": None, "imageUrl": og_image,
                "username": None, "fullName": None, "caption": None, "children": None}
    return None


def allowed_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (
            host.endswith(".cdninstagram.com") or
            host.endswith(".fbcdn.net")
        )
    except Exception:
        return False


def download_direct_media(media: dict, job_dir: Path, started_at: float) -> list[Path]:
    children = media.get("children") or [media]
    if len(children) > MAX_CAROUSEL_ITEMS:
        raise ValueError(f"Instagram returned more than {MAX_CAROUSEL_ITEMS} media items")
    files = []
    for index, item in enumerate(children, 1):
        enforce_job_limits(job_dir, started_at)
        target = item.get("videoUrl") if item.get("isVideo") else item.get("imageUrl")
        if not target:
            target = item.get("imageUrl") or item.get("videoUrl")
        if not target or not allowed_media_url(target):
            continue
        path = None
        try:
            response = requests.get(
                target,
                headers={"User-Agent": IG_BROWSER_UA},
                stream=True,
                timeout=(10, 30),
            )
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FILE_BYTES:
                raise ValueError("Instagram media exceeded the 250 MB safety limit")
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "video" in ctype or item.get("isVideo"):
                ext = ".mp4"
            elif "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            else:
                ext = ".jpg"
            path = job_dir / f"media_{index:03d}{ext}"
            total = 0
            with path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise ValueError("Instagram media exceeded the 250 MB safety limit")
                    handle.write(chunk)
                    enforce_job_limits(job_dir, started_at)
            if path.stat().st_size:
                files.append(path)
            enforce_job_limits(job_dir, started_at)
        except (ValueError, TimeoutError):
            if path and path.exists():
                path.unlink(missing_ok=True)
            raise
        except (requests.RequestException, OSError):
            if path and path.exists():
                path.unlink(missing_ok=True)
            continue
    return files


def run_direct_instagram(url: str, job_dir: Path, started_at: float) -> tuple[list[Path], dict]:
    media = resolve_instagram_direct(url)
    if not media:
        raise RuntimeError("Instagram's public media resolver returned no media")
    files = download_direct_media(media, job_dir, started_at)
    if not files:
        raise RuntimeError("Instagram returned media metadata, but the media files could not be downloaded")
    return files, media


def run_gallery_dl(url: str, job_dir: Path) -> tuple[list[Path], str]:
    """
    gallery-dl is used for Instagram post URLs because yt-dlp is primarily
    video/audio oriented and can fail on image-only posts/carousels.

    -D writes directly into the job directory.
    -f gives us predictable, safe names for the API.
    """
    base_cmd = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--quiet",
        "--no-mtime",
        "--destination",
        str(job_dir),
        "--filename",
        "{num:>03}.{extension}",
        "-o",
        "extractor.instagram.videos=true",
        "-o",
        "extractor.instagram.browser=chrome",
    ]

    # IMPORTANT: extractor.instagram.browser=chrome only emulates Chrome's
    # request headers/TLS. It does NOT load the user's Chrome login cookies.
    # For the authenticated fallback we must explicitly use gallery-dl's
    # --cookies-from-browser option. On Windows, Chrome should be fully
    # closed while its cookie database is being read.
    attempts = [
        ("anonymous", base_cmd + [url]),
        (
            "chrome-session",
            base_cmd + ["--cookies-from-browser", "chrome/instagram.com", url],
        ),
    ]
    last_detail = ""

    for attempt_name, cmd in attempts:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "gallery-dl is not installed. Run: pip install -r requirements.txt"
            )
        except subprocess.TimeoutExpired:
            last_detail = "The Instagram image extractor timed out"
            continue

        files = media_files(job_dir)
        if files:
            return files, (proc.stderr or proc.stdout or "").strip()

        last_detail = (proc.stderr or proc.stdout or "").strip()
        if attempt_name == "chrome-session" and "cookies" in last_detail.lower():
            last_detail = f"Chrome session attempt: {last_detail}"
        # Clear any partial output before the next extraction attempt.
        for child in list(job_dir.iterdir()):
            if child.is_file():
                child.unlink(missing_ok=True)

    detail = re.sub(r"\s+", " ", last_detail)
    if len(detail) > 500:
        detail = detail[-500:]
    raise RuntimeError(
        detail or
        "Instagram redirected the image request to login. If this persists, "
        "update gallery-dl or test while logged into Instagram in Chrome."
    )


def is_post_url(url: str) -> bool:
    path = url.lower().split("?", 1)[0]
    return "/p/" in path


def is_reel_url(url: str) -> bool:
    path = url.lower().split("?", 1)[0]
    return "/reel/" in path or "/reels/" in path


def build_result(job_id: str, files: list[Path], info: dict | None, url: str) -> dict:
    files = [p for p in files if p.exists() and p.stat().st_size > 0]
    if len(files) > MAX_CAROUSEL_ITEMS:
        raise RuntimeError(f"The download contains more than {MAX_CAROUSEL_ITEMS} media items")
    total_size = sum(p.stat().st_size for p in files)
    if total_size > MAX_TOTAL_JOB_BYTES:
        raise RuntimeError("The download exceeded the 500 MB total safety limit")
    if not files:
        raise RuntimeError("Downloaded media was empty")

    items = [
        {
            "filename": media.name,
            "type": media_kind(media.name),
            "download_url": f"/api/file/{job_id}/{media.name}",
        }
        for media in files
    ]

    entries = info.get("entries") if isinstance(info, dict) else None
    is_carousel = len(items) > 1 or bool(
        entries and len([entry for entry in entries if entry]) > 1
    )

    title = (info.get("title") if isinstance(info, dict) else None) or "Instagram media"

    if is_carousel:
        zip_path = Path(files[0].parent) / "instagram-media.zip"
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for media in files:
                archive.write(media, arcname=media.name)

        return {
            "ok": True,
            "title": title,
            "type": "carousel",
            "count": len(items),
            "filename": zip_path.name,
            "download_url": f"/api/file/{job_id}/{zip_path.name}",
            "preview_url": items[0]["download_url"],
            "items": items,
        }

    first_type = items[0]["type"]
    kind = "reel" if is_reel_url(url) and first_type == "video" else first_type

    return {
        "ok": True,
        "title": title,
        "type": kind,
        "count": 1,
        "filename": files[0].name,
        "download_url": items[0]["download_url"],
        "items": items,
    }


@app.get("/api/health")
def health():
    return {"ok": True, "service": "social-downloader"}


@app.post("/api/download")
def download(req: DownloadRequest, request: Request):
    cleanup_expired_jobs()
    enforce_rate_limit(request)

    url = str(req.url)
    if not validate_instagram(url):
        raise HTTPException(400, "For V1, please enter a public Instagram URL.")

    job_id = uuid.uuid4().hex
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()

    errors = []

    try:
        # First use a direct Instagram resolver for posts. It reads the media
        # payload Instagram serves to crawler clients / public post endpoints,
        # so image-only posts do not go through yt-dlp or gallery-dl.
        if is_post_url(url):
            try:
                files, media = run_direct_instagram(url, job_dir, started_at)
                info = {
                    "title": (media.get("username") or "Instagram") + " media",
                    "entries": [{"id": str(i)} for i, _ in enumerate(files)],
                }
                return build_result(job_id, files, info, url)
            except Exception as exc:
                errors.append(f"instagram-direct: {exc}")
                for child in list(job_dir.iterdir()):
                    if child.is_file():
                        child.unlink(missing_ok=True)

            # gallery-dl remains as a secondary image extractor.
            try:
                files, _ = run_gallery_dl(url, job_dir)
                enforce_job_limits(job_dir, started_at)
                return build_result(job_id, files, None, url)
            except Exception as exc:
                errors.append(f"gallery-dl: {exc}")
                for child in list(job_dir.iterdir()):
                    if child.is_file():
                        child.unlink(missing_ok=True)

        # Reels/videos, and a last-resort fallback for post URLs.
        try:
            files, info, _ = run_yt_dlp(url, job_dir)
            enforce_job_limits(job_dir, started_at)
            return build_result(job_id, files, info, url)
        except Exception as exc:
            errors.append(f"yt-dlp: {exc}")

        detail = " | ".join(errors)

        # Instagram can expose a very specific message when a post belongs to
        # a private/restricted account. Surface a friendly user-facing error
        # instead of leaking extractor internals such as yt-dlp/gallery-dl
        # stack messages. Keep the raw extractor details in the server process
        # output for debugging.
        lowered = detail.lower()
        private_markers = (
            "only available for registered users who follow this account",
            "only available to registered users who follow this account",
            "private account",
            "private instagram",
        )
        if any(marker in lowered for marker in private_markers):
            raise HTTPException(
                403,
                {
                    "code": "PRIVATE_ACCOUNT",
                    "title": "Private Instagram account",
                    "message": (
                        "This post belongs to a private or restricted Instagram account. "
                        "InstaGrab can only retrieve media that Instagram makes available "
                        "through public links."
                    ),
                },
            )

        # A login redirect without the explicit private-account marker is not
        # enough to claim that the account is private; Instagram also returns
        # login-required responses for rate limits and other access changes.
        if "rate-limit" in lowered or "rate limit" in lowered:
            raise HTTPException(
                429,
                {
                    "code": "RATE_LIMITED",
                    "title": "Instagram temporarily limited this request",
                    "message": "Please wait a little and try the public link again.",
                },
            )

        raise RuntimeError(detail or "No media could be retrieved")

    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(
            422,
            {
                "code": "MEDIA_UNAVAILABLE",
                "title": "Couldn't retrieve this Instagram post",
                "message": "Make sure the post is public and the URL is correct, then try again.",
            },
        )


@app.get("/api/file/{job_id}/{filename}")
def file(job_id: str, filename: str):
    cleanup_expired_jobs()

    if not safe_job_id(job_id) or not safe_filename(filename):
        raise HTTPException(404, "File not found or expired.")

    base = (DOWNLOAD_DIR / job_id).resolve()
    path = (base / filename).resolve()

    if base != path.parent or not path.is_file():
        raise HTTPException(404, "File not found or expired.")

    return FileResponse(
        path,
        filename=path.name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
