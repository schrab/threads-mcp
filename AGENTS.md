# AGENTS.md

MCP server exposing the Meta Threads API as tools. Wraps `graph.threads.net` —
no other runtime dependencies.

## Setup

```bash
pip install -e .
python tests/test_server.py    # mocked end-to-end suite, no network needed
```

Credentials go in `.env` at the repo root (see `.env.example`). It is gitignored
and loaded on import by `threads_mcp/server.py`. Real environment variables
always win over the file, so an MCP client injecting `THREADS_ACCESS_TOKEN` etc.
overrides `.env` without any special handling.

## The rule that matters most

**Verify every endpoint, field name, and parameter against Meta's docs before
changing them. Do not write API calls from memory.**

This codebase has been wrong twice in the same way, and both times the tests
passed and the error message pointed somewhere misleading:

| Written | Actual | Symptom |
|---|---|---|
| `POST /{id}/threads_publishing` | `POST /{id}/threads` and `/threads_publish` | `code 100 subcode 33`, "Object with ID … does not exist" — blamed the user profile |
| `fields=media_product,type,backlink` | `media_product_type`, `media_type` | "Tried accessing nonexisting field" on the tool's own default |

The first cost several rounds of chasing a permissions problem that did not
exist. Note that one unknown field fails the *entire* request, and that Meta's
"object does not exist" error can mean the *path* is wrong rather than the
object.

Docs: <https://developers.facebook.com/documentation/threads> — reference pages
are client-side rendered, so `webfetch` returns an empty shell and `.md` URLs
404. Use `websearch` with `type: "deep"`, which does return the rendered
parameter tables.

## Conventions

- `threads_mcp/api.py` is the HTTP layer — one method per endpoint, no tool
  definitions. `server.py` holds the `@mcp.tool` wrappers and does no I/O logic.
- Every tool returns `{"ok": True, **payload}` via `_ok()`.
- All eight write tools (`post_text`, `post_image`, `post_images`, `post_video`,
  `create_carousel_item`, `publish_carousel`, `reply`, `publish`) take optional
  `user_id` / `access_token` overrides, so one server instance can post for
  several accounts. Read tools that take an explicit id (`get_post`,
  `delete_post`) accept `access_token` only.
- Error handling is in `_check_payload`; raise `ThreadsAPIError` with the
  message and body rather than letting httpx exceptions escape.

## Testing

`tests/test_server.py` is a hand-rolled harness (no pytest) that mocks httpx
and drives the server through a real FastMCP client. Run it directly.

When adding a tool, assert the **endpoint path**, not just the response — a
wrong path that returns a valid mock will otherwise pass silently. The mock's
call recorder decodes bodies with `errors="replace"` because multipart uploads
carry binary.

## Gotchas

- **Container status** returns only `id,status,error_message`. Terminal values
  are `FINISHED`, `PUBLISHED`, `ERROR`, `EXPIRED`. These fields exist on
  *container* objects — querying a published post id fails.
- **Carousel children must be awaited** before creating the `CAROUSEL` parent,
  or it rejects them as "invalid, do not exist, or have expired". Use
  `wait_for_container`.
- **Carousels need 2–20 items**; one child fails outright.
- **`media_type` is required** on container creation and is not inferred by the
  API — omit it and the request fails.
- **Emoji count as UTF-8 bytes** against the 500-character post limit, so a
  visible character count understates the real cost.
- **Text posts don't need the media wait**; video containers do, and Meta
  recommends polling once a minute for at most five.
- **Images must be JPEG or PNG**, max 8 MB, width 320–1440. Not webp, GIF or
  HEIC — the API rejects them.
- **There is no upload endpoint.** Media is downloaded from a public URL at
  publish time. `upload_image()` in `api.py` hosts local files via catbox.moe
  (no account; its direct-link response is verified fetchable before publishing).
  catbox was picked by elimination — 0x0.st has uploads disabled, litterbox and
  tmp0 are unreachable, tmpfiles returns HTML instead of image bytes. If it
  breaks, re-test the alternatives rather than assuming the others are fine.

## Credentials

The Threads app ID/secret pair is required, **not** the Meta pair — Meta issues
both, and a Threads token signed with the Meta secret fails with `code=10` or
`452`. On a Threads token from the dashboard generator:

1. Enable the permission in the use case settings
2. **Then** generate a new token

Permissions bind at grant time, so an existing token is not upgraded in place.
The dashboard's user-token generator issues a long-lived (60-day) token
directly — do not run it through `th_exchange_token`, which fails with `452`.
Do not tell users to publish the app; testers grant permissions in Development
mode, and App Review is only needed for non-testers.

`threads_delete` currently returns `code 10` at runtime despite showing "Ready
for testing" in the dashboard. Undiagnosed. Deletions have to be done by hand.
