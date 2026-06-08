# File Chunk Uploader

Streams files from a local machine to GCP Cloud Storage using WebSocket chunking.

```
LOCAL MACHINE                          GCP CLOUD RUN
┌──────────────────┐                  ┌──────────────────────┐
│  client.py       │  WebSocket       │  server.py (FastAPI)  │
│  - Read file     │ ──────────────►  │  - Receive chunks     │
│  - Split chunks  │  binary frames   │  - Reassemble file    │
│  - Send chunks   │                  │  - Verify checksum    │
└──────────────────┘                  │  - Upload to GCS      │
                                      └──────────────────────┘
                                                │
                                                ▼
                                      ┌──────────────────────┐
                                      │  GCS Bucket           │
                                      └──────────────────────┘
```

## Protocol

Each file transfer is a 4-step handshake:

1. **Client → Server**: JSON `start` message with filename, size, chunk count, md5 checksum
2. **Server → Client**: JSON `{"status": "ready"}`
3. **Client → Server**: `N` binary frames, each prefixed with an 8-byte header `[chunk_index(4) | total_chunks(4)]`
4. **Client → Server**: JSON `end` message → Server verifies checksum → responds `{"status": "ok", "gcs_path": "..."}`

---

## Prerequisites

- Python 3.11+
- GCP project with a Cloud Storage bucket
- `gcloud` CLI installed and authenticated

---

## SERVER — Deploy to GCP Cloud Run

### 1. Create a GCS bucket (if needed)

```bash
gsutil mb -p YOUR_PROJECT_ID gs://YOUR_BUCKET_NAME
```

### 2. Set up permissions

The Cloud Run service account needs Storage Object Admin on your bucket:

```bash
# Find the default Cloud Run service account
SA="$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

gsutil iam ch serviceAccount:${SA}:objectAdmin gs://YOUR_BUCKET_NAME
```

### 3. Edit deploy.sh

```bash
cd server
# Edit PROJECT_ID, REGION, SERVICE_NAME, GCS_BUCKET_NAME in deploy.sh
chmod +x deploy.sh
./deploy.sh
```

The script will print the service URL. Note it — you'll use it as the `--server` argument in the client.

Cloud Run URL format: `https://file-chunk-server-xxxx-uc.a.run.app`
WebSocket URL: `wss://file-chunk-server-xxxx-uc.a.run.app/upload`

### Run locally (for testing)

```bash
cd server
pip install -r requirements.txt
GCS_BUCKET_NAME=your-bucket uvicorn server:app --host 0.0.0.0 --port 8000
```

Without `GCS_BUCKET_NAME`, the server runs in dry-run mode (receives and reassembles files but skips the GCS upload).

---

## CLIENT — Run locally

### 1. Install dependencies

```bash
cd client
pip install -r requirements.txt
```

### 2. Upload a single file

```bash
python client.py --input /path/to/myfile.zip --server wss://your-cloud-run-url/upload
```

### 3. Upload a folder (all files, recursively)

```bash
python client.py --input /path/to/my-folder --server wss://your-cloud-run-url/upload
```

### 4. Test against a local server

```bash
python client.py --input /path/to/file --server ws://localhost:8000/upload
```

---

## Configuration

| Variable | Where | Default | Description |
|---|---|---|---|
| `GCS_BUCKET_NAME` | server env | *(required)* | GCS bucket name |
| `UPLOAD_DIR` | server env | `/tmp/uploads` | Temp dir for chunk assembly |
| `MAX_FILE_SIZE_MB` | server env | `5000` | Max file size in MB |
| `--server` | client arg | `ws://localhost:8000/upload` | WebSocket server URL |

---

## Chunk size

Default chunk size is **1 MB** (adjustable via `CHUNK_SIZE` in `client.py`).
Larger chunks = fewer round-trips, better for big files on fast connections.
Smaller chunks = finer progress tracking, better on unreliable connections.

---

## Files

```
file-chunker/
├── client/
│   ├── client.py          # Upload script
│   └── requirements.txt
└── server/
    ├── server.py          # FastAPI WebSocket server
    ├── requirements.txt
    ├── Dockerfile
    └── deploy.sh          # GCP Cloud Run deploy script
```
