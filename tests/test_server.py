"""End-to-end tests for the Threads MCP server using a mocked Graph API."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from fastmcp import Client

import threads_mcp.server as server_mod
from threads_mcp.api import ThreadsAPIError, _appsecret_proof

CALLS = []


def handler(request: httpx.Request) -> httpx.Response:
    CALLS.append(
        {
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "params": dict(request.url.params),
            "body": request.content.decode() if request.content else "",
        }
    )
    path = request.url.path

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
        "threads_publish_carousel", {"children_ids": ids, "title": "trip", "text": "my trip"}
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
            }
            check("all expected tools registered", required <= names, required - names)

    run(all_tests)
    asyncio.run(list_check())
    # unit test appsecret proof
    import hmac, hashlib
    expected = hmac.new(b"s", b"t", hashlib.sha256).hexdigest()
    check("appsecret_proof correct", _appsecret_proof("s", "t") == expected)
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
