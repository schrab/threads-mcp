# Threads MCP Server

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server that lets **any
agent** post to your [Threads](https://www.threads.net) account using the official
[Meta Threads API](https://developers.facebook.com/docs/threads).

Ask your agent things like:

- *"Post 'good morning world' to my Threads"* → `threads_post_text`
- *"Share this image https://… with caption X on Threads"* → `threads_post_image`
- *"Reply on thread 1789… with 'great point!'"* → `threads_reply`
- *"Make a carousel of these 3 photos"* → `threads_create_carousel_item` × N + `threads_publish_carousel`
- *"How many views did my last post get?"* → `threads_list_posts` + `threads_get_insights`

## Tools provided (18)

| Tool | Purpose |
|---|---|
| `threads_check_config` | Verify env credentials are set (never leaks the token) |
| `threads_get_user_id` | Resolve a username → numeric `threads_profile_id` |
| `threads_get_profile` | Fetch profile fields |
| `threads_post_text` | Publish a text post (one call: create container + publish) |
| `threads_post_image` | Publish an image post from a public URL |
| `threads_post_video` | Publish a video post (polls until processing finishes) |
| `threads_create_carousel_item` | Create one carousel slide container |
| `threads_publish_carousel` | Assemble + publish a carousel (2–20 items) |
| `threads_reply` | Reply into an existing thread |
| `threads_create_container` | Low-level container creation (all publishing params) |
| `threads_get_container_status` | Poll container status (`FINISHED`/`IN_PROGRESS`/`FAILED`) |
| `threads_publish` | Publish a finished container |
| `threads_list_posts` | List your recent posts |
| `threads_get_post` | Fetch one post's details |
| `threads_get_replies` | List replies to a post |
| `threads_delete_post` | Delete one of your posts |
| `threads_get_insights` | Per-thread or account engagement metrics |
| `threads_list_keyword_hits` | Keyword mention monitoring (advanced permission) |

## 1. Get credentials (one-time)

Meta issues **two** ID/secret pairs for an app. You want the **Threads** pair
(*Settings → Basic* → *Threads app ID* / *Threads App secret*), not the Meta
pair — a Threads token signed with the Meta app secret fails with error 452.

1. Go to <https://developers.facebook.com> → **Create App** → use case **Other** →
   app type **Business**, then add the **Threads API** product.
2. Note the **Threads app ID** and **Threads app secret** under *Settings → Basic*.
3. Enable the permissions you need under *Use cases → Customize → Access the
   Threads API → Settings*. `threads_basic` is required; add
   `threads_content_publish` to post.
4. **App roles → Roles → Add People → Threads Tester**, add your Threads handle.
   Then, *from that Threads account*, accept the invite (Threads app →
   Settings → Account → Website permissions). Both halves are required.
5. Back in *Use cases → Customize → Access the Threads API → Settings*, scroll to
   the **user-token generator**, pick your tester account, and generate.
6. Put the token, your numeric user ID, and the Threads app secret in a `.env`
   file at the repo root (copy `.env.example`). The server loads it
   automatically on import; real environment variables take precedence.

The generator hands you a **long-lived (60-day) token directly** — there is no
short-lived → long-lived exchange to perform. Do not run it through
`th_exchange_token`; doing so fails with error 452.

> **Publishing is not required.** Testers can grant permissions at any time
> while the app is in Development mode. Publishing plus App Review is only
> needed to authorize accounts that are *not* testers.
>
> **Leave *Settings → Basic → Native or desktop app* off.** This server holds
> the secret in an env var; that toggle is for apps embedding the secret in a
> shipped binary, and enabling it makes Meta reject secret-signed calls.
>
> **If you need the full OAuth redirect flow** instead (e.g. to authorize
> non-tester accounts), the redirect URI must be **HTTPS**. Threads rejects
> `http://` URIs — including `http://localhost/...` — with `Insecure Login
> Blocked` (1349187) or `Invalid redirect_uri`. Host it somewhere public and
> pass it via `--redirect-uri`.

Refresh the long-lived token every ≤60 days with `grant_type=th_refresh_token`.


## 2. Install

```bash
pip install -e .          # installs the `threads-mcp` console script
# or just: pip install fastmcp httpx pydantic python-dotenv
```

### Secrets

Credentials are read from `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` and
`THREADS_APP_SECRET`. Resolution order at import time:

1. Real environment variables (what an MCP client injects) — always win.
2. `.env` in the repo root.
3. `.env` found by walking up from the current working directory.

`.env` is gitignored, so it is safe to keep real tokens there. Prefer it over
pasting secrets into a client's JSON config, which is more likely to get
synced or backed up. `threads_check_config` reports whether each value is set
without ever echoing it.

## 3. Wire it into your agent

The server reads `.env` from the repo root on its own, so most clients need no
credential config at all — just the command and the working directory.

### opencode (`~/.config/opencode/opencode.json`)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "threads": {
      "type": "local",
      "command": ["python", "-m", "threads_mcp.server"],
      "cwd": "/path/to/this/repo",
      "enabled": true
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "threads": {
      "command": "python",
      "args": ["-m", "threads_mcp.server"],
      "cwd": "/path/to/this/repo"
    }
  }
}
```

`cwd` matters unless you `pip install -e .` — an uninstalled package is only
importable from the directory containing it. If it is installed, use
`"command": "threads-mcp"` and drop `cwd`.

### Any other MCP client

The server speaks MCP over **stdio** by default:

```bash
python -m threads_mcp.server
```

To inject credentials from the client instead of `.env`, set
`THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` and `THREADS_APP_SECRET` in the
server process's environment — real variables always take precedence.

You can also expose it over HTTP/SSE with FastMCP:

```bash
python -c "from threads_mcp.server import mcp; mcp.run(transport='sse')"   # port 8000
```

Per-call overrides: every tool accepts optional `access_token` / `user_id`
arguments, so one server instance can post for multiple accounts.

## Posting images

The API has **no upload endpoint**. Threads downloads media from a public URL at
publish time, so a local file must be hosted for the duration of the call. Once
`threads_publish` returns an id, Meta holds its own copy on its CDN and the
hosted file is no longer needed.

`scripts/post_image.py` does the whole thing in one step — it uploads via
catbox.moe (no account), confirms Threads can fetch the URL, then publishes:

```bash
python scripts/post_image.py shot.png --text "the pig found a camera"
python scripts/post_image.py shot.png --no-post   # just host it, print the URL
```

Limits enforced by the API: **JPEG or PNG only** (not webp/GIF/HEIC), max
**8 MB**, width 320–1440 px (auto-scaled), aspect ratio max 10:1. Video must be
MOV/MP4 with no edit lists and the `moov` atom first, max 5 min and 1 GB.
Carousels take 2–20 items; create each slide with `threads_create_carousel_item`
using `is_carousel_item` and `media_type` of `IMAGE` or `VIDEO`, then assemble
with `threads_publish_carousel`.

## Example agent prompts once connected

- "Check whether the Threads MCP is configured."
- "Post to Threads: 'Shipped a new feature today 🚀 #buildinpublic'"
- "Post this chart image https://example.com/chart.png to Threads with a caption."
- "Reply 'Thanks! Sources in the thread.' to thread ID 17887…"
- "What were my top 5 posts last week and their view counts?"

## Notes & limits (per official API)

- Media must be at **publicly reachable URLs** (the API fetches them; there is no
  file-upload endpoint yet).
- Text posts support up to 500 characters (API limit); images jpg/png/webp,
  videos mp4/mov.
- Polls and quote-posts cannot be created via the API (read-only fields).
- Publishing is rate-limited (~250 posts/24h per user); video containers need
  polling until `is_finished` — handled automatically by `threads_post_video`.
- Testers can grant any permission while your app is in Development mode;
  App Review and publishing are only required for non-tester accounts.

## Development

```bash
python tests/test_server.py   # mocked end-to-end suite (no network needed)
```

## License

MIT
