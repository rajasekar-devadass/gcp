"""
File Chunk Server
-----------------
Receives chunked file uploads over WebSocket and stores them in a GCS bucket.

Environment variables:
  GCS_BUCKET_NAME   - GCS bucket to store files in (required)
  UPLOAD_DIR        - Temp directory for reassembling chunks (default: /tmp/uploads)
  MAX_FILE_SIZE_MB  - Max allowed file size in MB (default: 5000)
"""

import asyncio
import hashlib
import json
import logging
import os
import struct
import tempfile
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google.cloud import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="File Chunk Server")

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/tmp/uploads"))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_MB", 5000)) * 1024 * 1024

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_gcs_client():
    return storage.Client()


def upload_to_gcs(local_path: Path, destination_blob: str) -> str:
    client = get_gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(destination_blob)
    blob.upload_from_filename(str(local_path))
    return f"gs://{GCS_BUCKET_NAME}/{destination_blob}"


def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@app.get("/health")
async def health():
    return {"status": "ok", "bucket": GCS_BUCKET_NAME}


@app.websocket("/upload")
async def websocket_upload(ws: WebSocket):
    await ws.accept()
    client_host = ws.client.host if ws.client else "unknown"
    log.info(f"Client connected: {client_host}")

    try:
        while True:
            # ---- Wait for a START control message ----
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg.get("type") != "start":
                await ws.send_json({"status": "error", "error": "Expected 'start' message"})
                continue

            filename: str = msg["filename"]
            total_chunks: int = msg["total_chunks"]
            expected_checksum: str = msg["checksum"]
            file_size: int = msg["file_size"]

            log.info(f"Receiving: {filename} ({file_size:,} bytes, {total_chunks} chunks)")

            if file_size > MAX_FILE_SIZE:
                await ws.send_json({"status": "error", "error": "File exceeds max size"})
                continue

            # Signal readiness
            await ws.send_json({"status": "ready"})

            # ---- Receive chunks into a temp file ----
            # We use a dict to allow out-of-order delivery (defensive; client sends in order)
            chunks: dict[int, bytes] = {}

            while len(chunks) < total_chunks:
                raw_bytes = await ws.receive_bytes()
                # First 8 bytes = header: chunk_index (4) + total_chunks (4), little-endian
                chunk_index = struct.unpack_from("<I", raw_bytes, 0)[0]
                # total_chunks_from_header = struct.unpack_from("<I", raw_bytes, 4)[0]
                chunks[chunk_index] = raw_bytes[8:]

            # ---- Wait for END message ----
            end_raw = await ws.receive_text()
            end_msg = json.loads(end_raw)

            if end_msg.get("type") != "end":
                await ws.send_json({"status": "error", "error": "Expected 'end' message"})
                continue

            # ---- Reassemble file ----
            tmp_path = UPLOAD_DIR / filename
            tmp_path.parent.mkdir(parents=True, exist_ok=True)

            with open(tmp_path, "wb") as out:
                for i in range(total_chunks):
                    out.write(chunks[i])

            # ---- Verify checksum ----
            actual_checksum = md5_of_file(tmp_path)
            if actual_checksum != expected_checksum:
                log.error(f"Checksum mismatch for {filename}: expected {expected_checksum}, got {actual_checksum}")
                tmp_path.unlink(missing_ok=True)
                await ws.send_json({"status": "error", "error": "Checksum mismatch — file corrupted in transit"})
                continue

            log.info(f"Checksum OK for {filename}")

            # ---- Upload to GCS ----
            if not GCS_BUCKET_NAME:
                # Dry-run mode (no bucket configured)
                gcs_path = f"[dry-run] {tmp_path}"
                log.warning("GCS_BUCKET_NAME not set — skipping GCS upload (dry-run mode)")
            else:
                gcs_path = upload_to_gcs(tmp_path, filename)
                tmp_path.unlink(missing_ok=True)
                log.info(f"Uploaded to {gcs_path}")

            await ws.send_json({"status": "ok", "gcs_path": gcs_path})

    except WebSocketDisconnect:
        log.info(f"Client disconnected: {client_host}")
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        try:
            await ws.send_json({"status": "error", "error": str(e)})
        except Exception:
            pass
