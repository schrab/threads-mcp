"""Threads MCP server.

Exposes the Meta Threads API as MCP tools so any agent (Claude Desktop,
OpenAI Assistants via MCP bridges, custom agents, ...) can post to your
Threads account.

Run over stdio (default):
    python -m threads_mcp.server

Environment variables (all optional at import time; required per tool):
    THREADS_ACCESS_TOKEN  Long-lived Threads access token (60-day).
    THREADS_USER_ID       Numeric Threads profile id of the acting user.
    THREADS_APP_SECRET    Meta app secret, used to sign appsecret_proof.

Token setup (one-time, see README):
    1. Create a Threads "Device" app on developers.facebook.com.
    2. Short-lived token -> exchange for long-lived token.
    3. Export THREADS_ACCESS_TOKEN / THREADS_USER_ID.
"""



import os
from pathlib import Path
from typing import Annotated, Optional

from pydantic import Field

from dotenv import load_dotenv

from fastmcp import FastMCP

from threads_mcp.api import ThreadsAPIError, ThreadsClient

# MCP clients inject these directly; the repo-root .env is a convenience for
# running the server by hand. Real environment variables always win, since
# load_dotenv does not override them.
_REPO_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_REPO_ENV if _REPO_ENV.is_file() else None)

mcp = FastMCP(
    name="threads",
    instructions=(
        "Post and manage content on Threads (threads.net) using the official "
        "Meta Threads API. Use threads_post_text for quick text posts, "
        "threads_create_and_publish for image/video posts, and the container "
        "flow (threads_create_container -> threads_publish) for carousels or "
        "when you need to inspect status before publishing. Identity helpers: "
        "threads_get_user_id resolves a username to a profile id. If a tool "
        "returns an auth/config error, tell the user which environment "
        "variable to set instead of retrying blindly."
    ),
)

# Default client built from env vars; individual calls may override the token.


def _client(
    access_token: Optional[str] = None, user_id: Optional[str] = None
) -> ThreadsClient:
    return ThreadsClient(
        access_token=access_token
        or os.environ.get("THREADS_ACCESS_TOKEN")
        or None,
        user_id=user_id or os.environ.get("THREADS_USER_ID") or None,
        app_secret=os.environ.get("THREADS_APP_SECRET") or None,
    )





def _ok(payload: dict) -> dict:
    return {"ok": True, **payload}


# --------------------------------------------------------------------- identity


@mcp.tool(name="threads_check_config")
async def threads_check_config() -> dict:
    """Report whether credentials are configured (never reveals the token)."""
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    uid = os.environ.get("THREADS_USER_ID")
    secret = os.environ.get("THREADS_APP_SECRET")
    return _ok(
        {
            "access_token_set": bool(token),
            "user_id_set": bool(uid),
            "app_secret_set": bool(secret),
            "hint": (
                None
                if (token and uid)
                else "Set THREADS_ACCESS_TOKEN and THREADS_USER_ID env vars. "
                "See README for the token exchange flow."
            ),
        }
    )


@mcp.tool(name="threads_get_user_id")
async def threads_get_user_id(
    username: Annotated[
        str, Field(description="Threads username without the leading @, e.g. 'zuck'.")
    ],
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Resolve a Threads username to its numeric threads_profile_id."""
    client = _client(access_token)
    data = await client.lookup_user_id(username.lstrip("@"))
    return _ok(data)


@mcp.tool(name="threads_get_profile")
async def threads_get_profile(
    fields: Annotated[
        str,
        Field(
            description="Comma-separated fields, e.g. "
            "'id,username,name,bio_text,threads_profile_picture_url'."
        ),
    ] = "id,username,name,bio_text",
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Fetch profile fields for the configured (or given) Threads user."""
    client = _client(access_token, user_id)
    data = await client.get_user(user_id=user_id, fields=fields)
    return _ok(data)


# ------------------------------------------------------------------- publishing


@mcp.tool(name="threads_post_text")
async def threads_post_text(
    text: Annotated[str, Field(description="The text of the Threads post.")],
    reply_to_id: Annotated[
        Optional[str],
        Field(description="Thread id to reply to (creates a reply in that thread)."),
    ] = None,
    link_attachment: Annotated[
        Optional[str], Field(description="A link URL attachment for the post.")
    ] = None,
    location_id: Annotated[
        Optional[str], Field(description="Optional Threads location id tag.")
    ] = None,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Publish a plain-text post to Threads (create container + publish in one call)."""
    client = _client(access_token, user_id)
    data = await client.create_and_publish(
        text=text,
        reply_to_id=reply_to_id,
        link_attachment=link_attachment,
        location_id=location_id,
    )
    return _ok(data)


@mcp.tool(name="threads_post_image")
async def threads_post_image(
    image_url: Annotated[
        str,
        Field(
            description="Publicly accessible URL of the image to post "
            "(jpg/png/webp, max ~8MB)."
        ),
    ],
    text: Annotated[
        Optional[str], Field(description="Optional caption text for the image post.")
    ] = None,
    reply_to_id: Annotated[Optional[str], Field(description="Thread id to reply to.")] = None,
    link_attachment: Annotated[Optional[str], Field(description="Optional link attachment.")] = None,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Publish an image post to Threads from a public image URL."""
    client = _client(access_token, user_id)
    data = await client.create_and_publish(
        text=text,
        image_url=image_url,
        reply_to_id=reply_to_id,
        link_attachment=link_attachment,
    )
    return _ok(data)


@mcp.tool(name="threads_post_video")
async def threads_post_video(
    video_url: Annotated[
        str,
        Field(
            description="Publicly accessible URL of the mp4/mov video to post."
        ),
    ],
    text: Annotated[
        Optional[str], Field(description="Optional caption text for the video post.")
    ] = None,
    reply_to_id: Annotated[Optional[str], Field(description="Thread id to reply to.")] = None,
    poll_until_publish: Annotated[
        bool,
        Field(
            description="Poll container status until the video finishes "
            "processing before publishing (recommended for videos)."
        ),
    ] = True,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Publish a video post to Threads from a public video URL."""
    import asyncio

    client = _client(access_token, user_id)
    created = await client.create_container(
        media_type="VIDEO", video_url=video_url, text=text,
        reply_to_id=reply_to_id,
    )
    creation_id = created.get("id")
    if not creation_id:
        raise ThreadsAPIError(f"Video container creation failed: {created}")
    result = {"creation_id": creation_id}
    if poll_until_publish:
        # Meta's troubleshooting guide: poll once per minute, no more than 5 minutes.
        status: dict = {}
        for _ in range(5):
            status = await client.get_container_status(creation_id)
            # Terminal: EXPIRED, ERROR, FINISHED, PUBLISHED
            if status.get("status") in ("FINISHED", "PUBLISHED", "ERROR", "EXPIRED"):
                break
            await asyncio.sleep(60)
        result["status"] = status
        if status.get("status") in ("ERROR", "EXPIRED"):
            raise ThreadsAPIError(
                f"Video container did not become publishable: {status}", body=status
            )
    published = await client.publish_container(creation_id)
    result.update(published)
    return _ok(result)


@mcp.tool(name="threads_create_carousel_item")
async def threads_create_carousel_item(
    image_url: Annotated[Optional[str], Field(description="Image URL for this slide.")] = None,
    video_url: Annotated[Optional[str], Field(description="Video URL for this slide.")] = None,
    text: Annotated[
        Optional[str], Field(description="Alt/caption text for this slide.")
    ] = None,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Create one carousel item container; returns an id to pass to threads_publish_carousel."""
    client = _client(access_token, user_id)
    data = await client.create_container(
        is_carousel_item=True,
        image_url=image_url,
        video_url=video_url,
        text=text,
    )
    return _ok(data)


@mcp.tool(name="threads_publish_carousel")
async def threads_publish_carousel(
    children_ids: Annotated[
        list[str],
        Field(
            description="Ordered list of carousel item container ids "
            "(2-20 items) created via threads_create_carousel_item."
        ),
    ],
    title: Annotated[Optional[str], Field(description="Carousel title.")] = None,
    text: Annotated[
        Optional[str], Field(description="Caption text for the carousel post.")
    ] = None,
    publish: Annotated[
        bool, Field(description="Publish immediately after creating the carousel container.")
    ] = True,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Assemble carousel items into a carousel post and (optionally) publish it."""
    client = _client(access_token, user_id)
    container = await client.create_carousel_container(
        children_ids=children_ids, text=text
    )
    creation_id = container.get("id")
    result: dict = {"carousel_creation_id": creation_id}
    if publish and creation_id:
        result.update(await client.publish_container(creation_id))
    return _ok(result)


@mcp.tool(name="threads_reply")
async def threads_reply(
    reply_to_id: Annotated[
        str, Field(description="Id of the existing thread to reply to.")
    ],
    text: Annotated[str, Field(description="Reply text.")],
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Post a reply into an existing Threads conversation."""
    client = _client(access_token, user_id)
    data = await client.create_and_publish(text=text, reply_to_id=reply_to_id)
    return _ok(data)


@mcp.tool(name="threads_create_container")
async def threads_create_container(
    text: Annotated[Optional[str], Field(description="Post text.")] = None,
    media_type: Annotated[
        Optional[str],
        Field(description="TEXT | IMAGE | VIDEO | AUDIO | CAROUSEL."),
    ] = None,
    image_url: Annotated[Optional[str], Field(description="Public image URL.")] = None,
    video_url: Annotated[Optional[str], Field(description="Public video URL.")] = None,
    reply_to_id: Annotated[Optional[str], Field(description="Thread id to reply to.")] = None,
    is_carousel_item: Annotated[
        bool, Field(description="True to create a carousel child item.")
    ] = False,
    link_attachment: Annotated[Optional[str], Field(description="Link attachment URL.")] = None,
    location_id: Annotated[Optional[str], Field(description="Location tag id.")] = None,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Low-level: create a publishing container (returns creation id only)."""
    client = _client(access_token, user_id)
    data = await client.create_container(
        is_carousel_item=is_carousel_item,
        media_type=media_type,
        image_url=image_url,
        video_url=video_url,
        text=text,
        reply_to_id=reply_to_id,
        link_attachment=link_attachment,
        location_id=location_id,
    )
    return _ok(data)


@mcp.tool(name="threads_get_container_status")
async def threads_get_container_status(
    creation_id: Annotated[str, Field(description="Container id returned by create.")],
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Check processing status of a publishing container (FINISHED/IN_PROGRESS/FAILED)."""
    client = _client(access_token, user_id)
    data = await client.get_container_status(creation_id)
    return _ok(data)


@mcp.tool(name="threads_publish")
async def threads_publish(
    creation_id: Annotated[
        str, Field(description="Id of a finished container to publish.")
    ],
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Publish a previously created container (use after status is FINISHED)."""
    client = _client(access_token, user_id)
    data = await client.publish_container(creation_id)
    return _ok(data)


# ----------------------------------------------------------------------- reads


@mcp.tool(name="threads_list_posts")
async def threads_list_posts(
    limit: Annotated[int, Field(description="Max posts to return (1-100).", ge=1, le=100)] = 25,
    since: Annotated[
        Optional[int], Field(description="Unix timestamp lower bound (optional).")
    ] = None,
    until: Annotated[
        Optional[int], Field(description="Unix timestamp upper bound (optional).")
    ] = None,
    fields: Annotated[
        str,
        Field(
            description="Comma-separated fields to return.",
        ),
    ] = "id,permalink,text,timestamp,media_product,type,is_quote_post",
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """List recent Threads posts of the configured user."""
    client = _client(access_token, user_id)
    data = await client.get_threads(fields=fields, limit=limit, since=since, until=until)
    return _ok(data)


@mcp.tool(name="threads_get_post")
async def threads_get_post(
    thread_id: Annotated[str, Field(description="The thread (post) id.")],
    fields: Annotated[
        str,
        Field(description="Comma-separated fields to fetch."),
    ] = "id,permalink,text,timestamp,media_product,type,username,backlink",
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Fetch details of a single Threads post."""
    client = _client(access_token)
    data = await client.get_thread(thread_id, fields=fields)
    return _ok(data)


@mcp.tool(name="threads_get_replies")
async def threads_get_replies(
    thread_id: Annotated[str, Field(description="Parent thread id.")],
    fields: Annotated[str, Field(description="Comma-separated fields.")] = "id,text,timestamp,username",
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """List replies to a Threads post."""
    client = _client(access_token)
    data = await client.get_replies(thread_id, fields=fields)
    return _ok(data)


@mcp.tool(name="threads_delete_post")
async def threads_delete_post(
    thread_id: Annotated[str, Field(description="Id of the post to delete.")],
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Delete a Threads post owned by the authenticated user."""
    client = _client(access_token)
    data = await client.delete_thread(thread_id)
    return _ok(data)


@mcp.tool(name="threads_get_insights")
async def threads_get_insights(
    metric: Annotated[
        list[str],
        Field(
            description="Metrics, e.g. ['views','likes','replies','reposts',"
            "'quotes','followers_count_total']. Per-thread metrics: views, "
            "likes, replies, reposts, quotes, sent_impressions."
        ),
    ],
    thread_id: Annotated[
        Optional[str],
        Field(description="If provided, per-thread insights; otherwise account insights."),
    ] = None,
    since: Annotated[Optional[int], Field(description="Unix start (account insights).")] = None,
    until: Annotated[Optional[int], Field(description="Unix end (account insights).")] = None,
    user_id: Annotated[str, Field(description="Optional override of the configured value.")] = None,
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """Fetch engagement insights for a thread or the whole account."""
    client = _client(access_token, user_id)
    if thread_id:
        data = await client.get_thread_insights(thread_id, metric)
    else:
        data = await client.get_user_insights(metric, since=since, until=until)
    return _ok(data)


# --------------------------------------------------------- keyword monitoring


@mcp.tool(name="threads_list_keyword_hits")
async def threads_list_keyword_hits(
    search_keyword: Annotated[
        str, Field(description="Keyword to monitor mentions for.")
    ],
    access_token: Annotated[str, Field(description="Optional override of the configured value.")] = None,
) -> dict:
    """List keyword-monitoring hits (requires keyword searching permission)."""
    client = _client(access_token)
    uid = client.require_user_id()
    data = await client.get(
        f"{uid}/keywordhits",
        params=client._params({"search_keyword": search_keyword}),
    )
    return _ok(data)


# ------------------------------------------------------------------------ main


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
