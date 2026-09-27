#!/usr/bin/env python3
"""Upload a local image to a public host and publish it to Threads.

The Threads API has no upload endpoint — it downloads media from a public URL at
publish time. So a local file has to be hosted for the duration of the call.
Once threads_publish returns, Meta keeps its own copy on its CDN, so the hosted
file is no longer needed.

For multiple images, pass them to the threads_post_images MCP tool instead; it
publishes 2+ images as a carousel.

    python scripts/post_image.py shot.png --text "the pig found a camera"
    python scripts/post_image.py shot.png --no-post   # host only, print the URL
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
    args = p.parse_args()

    load_dotenv(REPO / ".env")

    try:
        url = asyncio.run(upload_image(str(args.image)))
    except ThreadsAPIError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(f"[+] hosted: {url}", file=sys.stderr)

    if args.no_post:
        print(url)
        return 0

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
