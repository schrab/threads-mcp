#!/usr/bin/env python3
"""Upload a local image to a public host and publish it to Threads.

The Threads API downloads media from a public URL. This script uploads the
image to Catbox, where its public URL may remain accessible after publication.

For multiple images, use the threads_post_images MCP tool.

    python scripts/post_image.py shot.png --allow-catbox-upload --text "a caption"
    python scripts/post_image.py shot.png --allow-catbox-upload --no-post
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from threads_mcp.api import ThreadsAPIError, ThreadsClient, upload_image

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image", type=Path)
    p.add_argument("--text", default=None, help="Caption. Optional.")
    p.add_argument(
        "--no-post", action="store_true", help="Upload and print the URL only."
    )
    p.add_argument(
        "--allow-catbox-upload", action="store_true",
        help="Allow this file to be uploaded to Catbox at a public URL that may persist.",
    )
    args = p.parse_args()
    if not args.allow_catbox_upload:
        p.error("Catbox makes the image public; pass --allow-catbox-upload to consent.")

    load_dotenv(REPO / ".env")
    client = ThreadsClient(
        os.environ.get("THREADS_ACCESS_TOKEN"),
        os.environ.get("THREADS_USER_ID"),
        os.environ.get("THREADS_APP_SECRET"),
    )
    try:
        if not args.no_post:
            client.require_user_id()
        url = asyncio.run(upload_image(str(args.image)))
    except ThreadsAPIError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] hosted: {url}", file=sys.stderr)

    if args.no_post:
        print(url)
        return 0

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
    if not media_id:
        print("[!] No published post ID returned. Check Threads before retrying.", file=sys.stderr)
        return 1
    try:
        detail = asyncio.run(client.get_thread(media_id, fields="permalink"))
        print(detail.get("permalink") or media_id)
    except ThreadsAPIError:
        print("[!] Post published, but its permalink could not be retrieved.", file=sys.stderr)
        print(media_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
