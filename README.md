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

1. Go to <https://developers.facebook.com> → **Create App** → use case **Other** →
   app type **Business**, then add the **Threads API** product.
2. In the Threads API product, register a **redirect URI** and generate an
   **access token** for your own account (this grants
   `threads_basic`, `threads_content_publish`, etc.). Or do OAuth manually:

   ```
   https://auth.threadapp.com/auth/connect?client_id=<APP_ID>&redirect_uri=<URI>&response_type=code&scope=threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies,threads_manage_insights
   ```
3. Mint a long-lived (60-day) token + user id automatically:

   ```bash
   python scripts/setup_token.py --client-id <APP_ID> --client-secret <APP_SECRET> --code <OAUTH_CODE>
   # prints: export THREADS_USER_ID=... / THREADS_ACCESS_TOKEN=... / THREADS_APP_SECRET=...
   ```
4. Put those three values in your environment (copy `.env.example`).

> The short-lived token expires in ~1 hour; always exchange it with
> `grant_type=th_exchange_token` (the script does this for you). Refresh the
> long-lived token every ≤60 days with `grant_type=th_refresh_token`.

## 2. Install

```bash
pip install -e .          # installs the `threads-mcp` console script
# or just: pip install fastmcp httpx pydantic
```

## 3. Wire it into your agent

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "threads": {
      "command": "python",
      "args": ["-m", "threads_mcp.server"],
      "cwd": "/path/to/this/repo",
      "env": {
        "THREADS_ACCESS_TOKEN": "<long-lived token>",
        "THREADS_USER_ID": "<numeric id>",
        "THREADS_APP_SECRET": "<app secret>"
      }
    }
  }
}
```

(If installed with `pip install -e .`, use `"command": "threads-mcp"` instead.)

### Any other MCP client

The server speaks MCP over **stdio** by default:

```bash
export THREADS_ACCESS_TOKEN=... THREADS_USER_ID=... THREADS_APP_SECRET=...
python -m threads_mcp.server
```

You can also expose it over HTTP/SSE with FastMCP:

```bash
python -c "from threads_mcp.server import mcp; mcp.run(transport='sse')"   # port 8000
```

Per-call overrides: every tool accepts optional `access_token` / `user_id`
arguments, so one server instance can post for multiple accounts.

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
- Your app must be approved for the permissions you request; for personal use
  with a Device app in dev mode, your own test-user account works immediately.

## Development

```bash
python tests/test_server.py   # mocked end-to-end suite (no network needed)
```

## License

MIT
