#!/usr/bin/env python3
"""Upload a local image to a public host and publish it to Threads.

The Threads API has no upload endpoint — it downloads media from a public URL
at publish time. So a local file has to be hosted for the duration of the call.
This script does both in one step via catbox.moe (no account required), verifies
the URL is publicly fetchable, then publishes and prints the permalink.

    python scripts/post_image.py shot.png --text "the pig found a camera"

Once threads_publish returns, Meta holds its own copy on its CDN, so the hosted
file is no longer needed. Use --no-post to upload and print the URL without
publishing, if you want to host it yourself.
"""

import argparse
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UPLOAD_URL = "https://catbox.moe/user/api.php"
MAX_BYTES = 8 * 1024 * 1024  # 8 MB, per the API reference
ALLOWED = {"image/jpeg", "image/png"}


def _post_file(path: Path) -> str:
    boundary = "----threads-mcp-upload-boundary"
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="reqtype"\r\n\r\n',
            b"fileupload\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="fileToUpload"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mimetype}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    req = urllib.request.Request(
        UPLOAD_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode().strip()


def _verify(url: str) -> str:
    """Confirm Threads can actually fetch it before we spend a publish call."""
    req = urllib.request.Request(url, headers={"User-Agent": "threads-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        ctype = r.headers.get("Content-Type", "")
        size = r.headers.get("Content-Length", "?")
    return f"{ctype}, {size} bytes"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image", type=Path)
    p.add_argument("--text", default=None, help="Caption. Optional.")
    p.add_argument(
        "--no-post", action="store_true", help="Upload and print the URL only."
    )
    args = p.parse_args()

    if not args.image.is_file():
        print(f"[!] no such file: {args.image}", file=sys.stderr)
        return 1
    size = args.image.stat().st_size
    if size > MAX_BYTES:
        print(
            f"[!] {args.image.name} is {size} bytes; the API limit is "
            f"{MAX_BYTES} (8 MB).",
            file=sys.stderr,
        )
        return 1
    mime = mimetypes.guess_type(args.image.name)[0]
    if mime not in ALLOWED:
        print(
            f"[!] format {mime or 'unknown'} is not accepted. Use JPEG or PNG.",
            file=sys.stderr,
        )
        return 1

    try:
        url = _post_file(args.image)
    except urllib.error.HTTPError as e:
        print(f"[!] upload failed ({e.code}): {e.read().decode()[:200]}", file=sys.stderr)
        return 1

    if not url.startswith("http"):
        print(f"[!] upload returned no URL: {url!r}", file=sys.stderr)
        return 1

    try:
        seen = _verify(url)
    except urllib.error.HTTPError as e:
        print(f"[!] hosted URL is not publicly fetchable ({e.code})", file=sys.stderr)
        return 1
    print(f"[+] hosted {seen}: {url}", file=sys.stderr)

    if args.no_post:
        print(url)
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import asyncio

    from threads_mcp.api import ThreadsAPIError, ThreadsClient
    from dotenv import load_dotenv

    import os

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = ThreadsClient(
        os.environ.get("THREADS_ACCESS_TOKEN"),
        os.environ.get("THREADS_USER_ID"),
        os.environ.get("THREADS_APP_SECRET"),
    )

    async def go() -> dict:
        created = await client.create_container(
            media_type="IMAGE", image_url=url, text=args.text
        )
        return await client.publish_container(created["id"])

    try:
        result = asyncio.run(go())
    except ThreadsAPIError as e:
        print(f"[!] publish failed: {e}", file=sys.stderr)
        return 1

    media_id = result.get("id")
    detail = asyncio.run(client.get_thread(media_id, fields="permalink"))
    print(detail.get("permalink", media_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
