# InstaGrab V1.0

Instagram media downloader backend + frontend.

## Current capabilities

- Public Instagram Reels/videos
- Public single photos
- Public image carousels
- Carousel ZIP downloads
- Automatic media-type detection
- Friendly private-account and invalid-URL errors

## Backend hardening in V1.0

- 60-minute automatic job expiry
- Background cleanup every 10 minutes
- Per-client in-memory rate limiting: 10 download requests/minute
- 250 MB maximum individual media file
- 500 MB maximum total job size
- 20 maximum carousel items
- 120-second overall processing guard for direct media
- Strict Instagram URL host validation
- Strict generated job/file path validation
- No-store headers on generated media responses
- Temporary files remain isolated per job and are removed after expiry

## Run locally

### Backend

```powershell
cd backend
venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

The API runs on `http://127.0.0.1:8000` and Swagger is at `/docs`.
