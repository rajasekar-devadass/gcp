"""
File Chunk Client
-----------------
Usage:
  python client.py --input /path/to/file_or_folder --server ws://localhost:8000/upload

Chops each file into 1MB chunks and streams them to the server over WebSocket.
"""

import asyncio
import json
import os
import sys
import argparse
import hashlib
from pathlib import Path
import websockets
from tqdm import tqdm

CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB


def collect_files(input_path: str) -> list[Path]:
    p = Path(input_path)
    if p.is_file():
        return [p]
    elif p.is_dir():
        return sorted(f for f in p.rglob("*") if f.is_file())
    else:
        raise ValueError(f"Path not found: {input_path}")


def file_checksum(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def send_file(ws, file_path: Path, base_dir: Path):
    file_size = file_path.stat().st_size
    total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE if file_size > 0 else 1
    relative_path = str(file_path.relative_to(base_dir))
    checksum = file_checksum(file_path)

    # --- Send START message ---
    await ws.send(json.dumps({
        "type": "start",
        "filename": relative_path,
        "file_size": file_size,
        "total_chunks": total_chunks,
        "checksum": checksum,
    }))

    ack = json.loads(await ws.recv())
    if ack.get("status") != "ready":
        print(f"  Server not ready: {ack}")
        return False

    # --- Stream chunks ---
    chunk_index = 0
    with open(file_path, "rb") as f:
        with tqdm(total=file_size, unit="B", unit_scale=True,
                  desc=f"  {relative_path}", leave=False) as bar:
            while True:
                data = f.read(CHUNK_SIZE)
                if not data:
                    break

                # Header: 4-byte chunk index + 4-byte total chunks (little-endian)
                header = chunk_index.to_bytes(4, "little") + total_chunks.to_bytes(4, "little")
                await ws.send(header + data)

                bar.update(len(data))
                chunk_index += 1

    # --- Send END message ---
    await ws.send(json.dumps({
        "type": "end",
        "filename": relative_path,
        "checksum": checksum,
    }))

    result = json.loads(await ws.recv())
    if result.get("status") == "ok":
        print(f"  ✓ {relative_path} ({file_size:,} bytes) → {result.get('gcs_path')}")
        return True
    else:
        print(f"  ✗ {relative_path} failed: {result.get('error')}")
        return False


async def main(input_path: str, server_url: str):
    base_dir = Path(input_path)
    if base_dir.is_file():
        base_dir = base_dir.parent

    files = collect_files(input_path)
    print(f"Found {len(files)} file(s) to upload → {server_url}\n")

    async with websockets.connect(server_url, max_size=None) as ws:
        success = 0
        for file_path in files:
            ok = await send_file(ws, file_path, base_dir)
            if ok:
                success += 1

    print(f"\nDone: {success}/{len(files)} file(s) uploaded successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chunk-upload files over WebSocket")
    parser.add_argument("--input", required=True, help="File or folder to upload")
    parser.add_argument("--server", default="ws://localhost:8000/upload",
                        help="WebSocket server URL (default: ws://localhost:8000/upload)")
    args = parser.parse_args()

    asyncio.run(main(args.input, args.server))
