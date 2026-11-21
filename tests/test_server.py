"""End-to-end tests for the Threads MCP server using a mocked Graph API."""

import asyncio
import json
import os
import sys
from base64 import b64decode
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from fastmcp import Client

import threads_mcp.server as server_mod
from threads_mcp.api import ThreadsAPIError, _appsecret_proof

CALLS = []
RESPONSES = {}
PNG = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")


def handler(request: httpx.Request) -> httpx.Response:
    CALLS.append(
        {
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "params": dict(request.url.params),
            # multipart uploads carry binary; never let that break the recorder
            "body": request.content.decode(errors="replace") if request.content else "",
        }
    )
    path = request.url.path
    if (request.method, path) in RESPONSES:
        return RESPONSES[request.method, path](request)

    # image hosting (catbox) — upload + public fetch verification
    if request.url.host == "catbox.moe":
        return httpx.Response(200, text="https://files.catbox.moe/test-upload.png")
    if request.url.host == "files.catbox.moe":
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\nfake",
            headers={"Content-Type": "image/png"},
        )
    if path.endswith("/threads_profile_id"):
        return httpx.Response(200, json={"threads_profile_id": "17841406385576486"})
    # POST /{user-id}/threads_publish  (publish step)
    if request.method == "POST" and path.endswith("/threads_publish"):
        return httpx.Response(200, json={"id": "9001"})
    # POST /{user-id}/threads  (media container creation)
    if request.method == "POST" and path.endswith("/threads"):
        return httpx.Response(200, json={"id": "container-123"})
    if "/insights" in path:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"name": "views", "total_value": {"value": 4200}, "period": "lifetime"}
                ]
            },
        )
    if path.endswith("/threads"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "9001", "text": "hello world", "permalink": "https://www.threads.net/@me/post/9001"}
                ],
                "paging": {"next": "https://graph.threads.net/v1.0/me/threads?after=x"},
            },
        )
    if request.method == "DELETE":
        return httpx.Response(200, json={"success": True})
    # container status / generic get
    if "container-123" in path:
        return httpx.Response(
            200,
            json={"id": "container-123", "status": "FINISHED"},
        )
    return httpx.Response(200, json={"id": "17841406385576486", "username": "zuck"})


def install_mock():
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    httpx.AsyncClient = patched
    import threads_mcp.api as api_mod

    api_mod.httpx = httpx


install_mock()

os.environ["THREADS_ACCESS_TOKEN"] = "test-token"
os.environ["THREADS_USER_ID"] = "17841406385576486"
os.environ["THREADS_APP_SECRET"] = "test-secret"


def run(coro_fn):
    async def go():
        async with Client(server_mod.mcp) as c:
            return await coro_fn(c)

    return asyncio.run(go())


def data_of(res):
    # fastMCP returns list of content blocks or structured content
    if res.structured_content is not None:
        return res.structured_content
    block = res.content[0]
    return json.loads(block.text)


PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {extra}")


async def t_check_config(c):
    res = await c.call_tool("threads_check_config")
    d = data_of(res)
    check("check_config reports token set", d.get("access_token_set") is True, d)


async def t_get_user_id(c):
    res = await c.call_tool("threads_get_user_id", {"username": "@zuck"})
    d = data_of(res)
    check("get_user_id resolves profile id", d.get("threads_profile_id") == "17841406385576486", d)
    call = CALLS[-1]
    check("uses graph.threads.net", "graph.threads.net" in call["url"], call["url"])
    check("appsecret_proof sent", "appsecret_proof" in call["params"], call["params"].keys())


async def t_post_text(c):
    before = len(CALLS)
    res = await c.call_tool("threads_post_text", {"text": "Hello from MCP!"})
    d = data_of(res)
    calls = CALLS[before:]
    check("post_text creates container then publishes", len(calls) == 2, calls)
    create_body = calls[0]["body"]
    check("container POST has text", "Hello+from+MCP%21" in create_body or "Hello from MCP" in create_body, create_body)
    check("publish uses creation_id", "creation_id=container-123" in calls[1]["body"], calls[1]["body"])
    check("returns published id", d.get("id") == "9001", d)
    # Endpoints per https://developers.facebook.com/documentation/threads/reference/publishing
    check("container uses POST /threads", calls[0]["path"].endswith("/threads"), calls[0]["path"])
    check("publish uses POST /threads_publish", calls[1]["path"].endswith("/threads_publish"), calls[1]["path"])
    check("media_type is sent (required by API)", "media_type=TEXT" in create_body, create_body)
    check("no legacy threads_publishing endpoint", "threads_publishing" not in calls[0]["path"] and "threads_publishing" not in calls[1]["path"])


async def t_post_image(c):
    before = len(CALLS)
    res = await c.call_tool(
        "threads_post_image",
        {"image_url": "https://example.com/a.jpg", "text": "caption"},
    )
    calls = CALLS[before:]
    body = calls[0]["body"]
    check("image media_type IMAGE", "media_type=IMAGE" in body, body)
    check("image_url passed", "image_url=https%3A%2F%2Fexample.com%2Fa.jpg" in body, body)
    check("image published", data_of(res).get("id") == "9001")


async def t_reply(c):
    before = len(CALLS)
    await c.call_tool("threads_reply", {"reply_to_id": "9000", "text": "nice post"})
    body = CALLS[before]["body"]
    check("reply passes reply_to_id", "reply_to_id=9000" in body, body)


async def t_carousel(c):
    before = len(CALLS)
    r1 = await c.call_tool("threads_create_carousel_item", {"image_url": "https://e.com/1.jpg"})
    r2 = await c.call_tool("threads_create_carousel_item", {"image_url": "https://e.com/2.jpg"})
    ids = [data_of(r1)["id"], data_of(r2)["id"]]
    check("carousel items flagged", "is_carousel_item=true" in CALLS[before]["body"], CALLS[before]["body"])
    res = await c.call_tool(
        "threads_publish_carousel", {"children_ids": ids, "text": "my trip"}
    )
    carousel_call = CALLS[-2]
    check("carousel children joined", f"children={ids[0]}%2C{ids[1]}" in carousel_call["body"], carousel_call["body"])
    check("carousel published", data_of(res).get("id") == "9001", data_of(res))


async def t_list_posts(c):
    res = await c.call_tool("threads_list_posts", {"limit": 10})
    d = data_of(res)
    check("list posts returns data", d.get("data") and d["data"][0]["text"] == "hello world", d)


async def t_insights(c):
    res = await c.call_tool("threads_get_insights", {"metric": ["views"], "thread_id": "9001"})
    d = data_of(res)
    check("insights returned", d.get("data")[0]["name"] == "views", d)


async def t_delete(c):
    res = await c.call_tool("threads_delete_post", {"thread_id": "9001"})
    check("delete success", data_of(res).get("success") is True, data_of(res))
    check("delete used DELETE verb", CALLS[-1]["method"] == "DELETE", CALLS[-1])


async def t_error_surface(c):
    # Temporarily break auth to see the error surfaced to the MCP client
    saved = os.environ.pop("THREADS_ACCESS_TOKEN")
    try:
        raised = False
        try:
            await c.call_tool("threads_post_text", {"text": "should fail"})
        except Exception as e:
            raised = "THREADS_ACCESS_TOKEN" in str(e)
        check("missing token surfaces actionable error", raised)
    finally:
        os.environ["THREADS_ACCESS_TOKEN"] = saved


async def t_status(c):
    res = await c.call_tool("threads_get_container_status", {"creation_id": "container-123"})
    d = data_of(res)
    check("container status FINISHED", d.get("status") == "FINISHED", d)


def _write_png(directory, name="test.png"):
    path = Path(directory) / name
    path.write_bytes(PNG)
    return str(path)


async def _tool_error(c, tool, args):
    from fastmcp.exceptions import ToolError

    try:
        await c.call_tool(tool, args)
    except ToolError as e:
        return str(e)
    return ""


async def t_post_images_guards(c):
    with TemporaryDirectory() as directory:
        good = _write_png(directory)
        args = {"images": [good]}
        before = len(CALLS)
        error = await _tool_error(c, "threads_post_images", args)
        check("local images require explicit Catbox opt-in", "allow_catbox_upload" in error and len(CALLS) == before)

        args["allow_catbox_upload"] = True
        for setting in ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID"):
            with patch.dict(os.environ):
                del os.environ[setting]
                before = len(CALLS)
                error = await _tool_error(c, "threads_post_images", args)
                check(f"missing {setting} blocks upload", setting in error and len(CALLS) == before)

        bad = Path(directory) / "bad.png"
        bad.write_bytes(PNG[:20])
        args["images"] = [good, str(bad)]
        before = len(CALLS)
        error = await _tool_error(c, "threads_post_images", args)
        check("invalid image in batch prevents every upload", "bad.png" in error and len(CALLS) == before)


async def t_post_images_permalink_failure(c):
    with TemporaryDirectory() as directory, patch.dict(RESPONSES, {
        ("GET", "/v1.0/9001"): lambda _: httpx.Response(503, json={"error": {"message": "Unavailable"}}),
    }):
        before = len(CALLS)
        args = {"images": [_write_png(directory)], "allow_catbox_upload": True}
        try:
            d = data_of(await c.call_tool("threads_post_images", args))
        except Exception:
            d = {}
        publishes = [call for call in CALLS[before:] if call["path"].endswith("/threads_publish")]
        check("published image ID survives a permalink lookup failure", d.get("ok") and d.get("id") == "9001", d)
        check("permalink lookup failure does not publish again", len(publishes) == 1)


async def t_post_video_processing(c):
    for status, expect_publish, published in (
        ("IN_PROGRESS", False, False),
        ("PUBLISHED", False, True),
        ("FINISHED", True, True),
    ):
        with patch.dict(RESPONSES, {
            ("GET", "/v1.0/container-123"): lambda _: httpx.Response(200, json={"status": status}),
        }), patch("asyncio.sleep", new_callable=AsyncMock):
            before = len(CALLS)
            res = await c.call_tool("threads_post_video", {"video_url": "https://example.com/movie.mp4"})
            d = data_of(res)
            did_publish = any(call["path"].endswith("/threads_publish") for call in CALLS[before:])
            check(f"video {status}: publish call only if ready", did_publish == expect_publish)
            check(f"video {status}: reports outcome and creation ID", d.get("published") is published and d.get("creation_id") == "container-123", d)


async def t_post_video_no_wait(c):
    with patch.dict(RESPONSES, {
        ("GET", "/v1.0/container-123"): lambda _: httpx.Response(200, json={"status": "IN_PROGRESS"}),
    }):
        before = len(CALLS)
        res = await c.call_tool("threads_post_video", {
            "video_url": "https://example.com/movie.mp4", "poll_until_publish": False,
        })
        calls = CALLS[before:]
        check("no-wait video checks status once", sum(call["method"] == "GET" for call in calls) == 1)
        check("no-wait video does not publish a pending container", not any(
            call["path"].endswith("/threads_publish") for call in calls
        ) and data_of(res).get("published") is False)


async def t_carousel_limits(c):
    for count in (0, 1, 21):
        before = len(CALLS)
        error = await _tool_error(c, "threads_publish_carousel", {"children_ids": ["container-123"] * count})
        check(f"carousel with {count} children fails before any requests", "2" in error and "20" in error and len(CALLS) == before)


async def t_http_errors():
    from threads_mcp.api import ThreadsClient

    client = ThreadsClient(access_token="test-token")
    for verb, response, expected in (
        ("GET", httpx.Response(503, json={"message": "Unavailable"}), "503"),
        ("POST", httpx.Response(502, text="Bad Gateway"), "502"),
        ("DELETE", httpx.Response(403, json={"error": {"message": "Permission denied", "code": 10}}), "code=10"),
        ("GET", httpx.Response(200, text="Not JSON"), "JSON"),
    ):
        with patch.dict(RESPONSES, {(verb, "/v1.0/test"): lambda _: response}):
            try:
                await getattr(client, verb.lower())("test")
            except ThreadsAPIError as e:
                ok = expected in str(e) and e.status_code == response.status_code
            except ValueError:
                ok = False
            else:
                ok = False
        check(f"{verb} HTTP {response.status_code}: surfaces typed API error", ok)

    def timeout(request):
        raise httpx.ReadTimeout(f"Timed out at {request.url}", request=request)

    with patch.dict(RESPONSES, {("GET", "/v1.0/test"): timeout}):
        try:
            await client.get("test", params={"access_token": "test-token"})
        except ThreadsAPIError as e:
            ok = "ReadTimeout" in str(e) and "test-token" not in str(e)
        except httpx.RequestError:
            ok = False
        else:
            ok = False
    check("transport errors become actionable errors without credentials", ok)


async def t_post_images_single(c):
    before = len(CALLS)
    with TemporaryDirectory() as directory:
        res = await c.call_tool("threads_post_images", {
            "images": [_write_png(directory)], "text": "hi", "allow_catbox_upload": True,
        })
    calls = CALLS[before:]
    creates = [x for x in calls if "media_type" in x["body"]]
    check("post_images: exactly one container created", len(creates) == 1, calls)
    body = creates[0]["body"] if creates else ""
    check("post_images: single image -> media_type=IMAGE", "media_type=IMAGE" in body, body)
    check("post_images: uses hosted url", "files.catbox.moe" in body, body)
    check("post_images: kind=image", data_of(res).get("kind") == "image", data_of(res))


async def t_post_images_carousel(c):
    before = len(CALLS)
    with TemporaryDirectory() as directory:
        res = await c.call_tool("threads_post_images", {
            "images": [_write_png(directory, "a.png"), _write_png(directory, "b.png")],
            "text": "hi", "allow_catbox_upload": True,
        })
    calls = CALLS[before:]
    joined = "".join(x["body"] for x in calls)
    check("post_images: children flagged carousel", "is_carousel_item=true" in joined, joined)
    check("post_images: parent is CAROUSEL", "media_type=CAROUSEL" in joined, joined)
    check("post_images: kind=carousel", data_of(res).get("kind") == "carousel", data_of(res))


async def t_upload_rejects():
    from threads_mcp.api import ThreadsAPIError, upload_image
    ok = False
    try:
        await upload_image("/tmp/definitely-not-here.png")
    except ThreadsAPIError:
        ok = True
    check("upload_image rejects missing file", ok)

    bad = os.path.join(os.path.dirname(__file__), "..", "README.md")
    ok = False
    try:
        await upload_image(bad)
    except ThreadsAPIError as e:
        ok = "JPEG" in str(e)
    check("upload_image rejects non-image format", ok)


def main():
    async def all_tests(c):
        for fn in (
            t_check_config,
            t_get_user_id,
            t_post_text,
            t_post_image,
            t_reply,
            t_carousel,
            t_list_posts,
            t_insights,
            t_delete,
            t_status,
            t_error_surface,
            t_post_images_guards,
            t_post_images_permalink_failure,
            t_post_video_processing,
            t_post_video_no_wait,
            t_carousel_limits,
            t_post_images_single,
            t_post_images_carousel,
        ):
            await fn(c)

    # tool listing sanity
    async def list_check():
        async with Client(server_mod.mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}
            required = {
                "threads_post_text", "threads_post_image", "threads_post_video",
                "threads_reply", "threads_publish_carousel", "threads_list_posts",
                "threads_get_insights", "threads_delete_post", "threads_check_config",
                "threads_post_images",
            }
            check("all expected tools registered", required <= names, required - names)

    run(all_tests)
    asyncio.run(list_check())
    asyncio.run(t_upload_rejects())
    asyncio.run(t_http_errors())
    # unit test appsecret proof
    import hmac, hashlib
    expected = hmac.new(b"s", b"t", hashlib.sha256).hexdigest()
    check("appsecret_proof correct", _appsecret_proof("s", "t") == expected)
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
