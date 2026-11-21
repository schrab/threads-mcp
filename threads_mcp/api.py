"""Thin async client for the Meta Threads API.

Endpoints implemented per:
https://developers.facebook.com/docs/threads/reference

All requests go through graph.threads.net using appsecret_proof when a
THREADS_APP_SECRET is configured (required by Meta for server-side calls).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple, Optional

import httpx
from PIL import Image

GRAPH_BASE = "https://graph.threads.net/v1.0"
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}


class ThreadsAPIError(RuntimeError):
    """Raised when the Threads Graph API returns an error."""

    def __init__(self, message: str, status_code: int = 0, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class LocalImage(NamedTuple):
    name: str
    content: bytes
    mime: str


def read_local_image(path: str) -> LocalImage:
    """Validate and snapshot the bytes before they can be sent to a host."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ThreadsAPIError(f"No such image file: {p}")
    try:
        with p.open("rb") as f:
            content = f.read(IMAGE_MAX_BYTES + 1)
    except OSError as e:
        raise ThreadsAPIError(f"Could not read image file: {p.name}") from e
    if len(content) > IMAGE_MAX_BYTES:
        raise ThreadsAPIError(f"{p.name} exceeds the 8 MB image limit.")

    try:
        with Image.open(BytesIO(content), formats=list(IMAGE_FORMATS)) as image:
            mime = IMAGE_FORMATS[image.format]
            image.verify()
        # verify() checks container integrity, but does not decode pixel data.
        with Image.open(BytesIO(content), formats=list(IMAGE_FORMATS)) as image:
            image.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise ThreadsAPIError(f"{p.name} is not a valid JPEG or PNG image.") from e
    return LocalImage(p.name, content, mime)


async def upload_image(path: str) -> str:
    """Validate a local image and make it available at a public Catbox URL."""
    return await upload_validated_image(read_local_image(path))


async def upload_validated_image(image: LocalImage) -> str:
    """Upload validated bytes to Catbox and check that the public URL works."""
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            CATBOX_UPLOAD_URL,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (image.name, image.content, image.mime)},
        )
    url = r.text.strip()
    if r.status_code != 200 or not url.startswith("http"):
        raise ThreadsAPIError(f"Image upload failed: {r.text[:200]}")
    async with httpx.AsyncClient(timeout=60.0) as c:
        head = await c.get(url, headers={"User-Agent": "threads-mcp/1.0"})
    if head.status_code != 200 or not head.content:
        raise ThreadsAPIError(
            f"Hosted image is not publicly fetchable (HTTP {head.status_code}): {url}"
        )
    return url


def _appsecret_proof(app_secret: str, access_token: str) -> str:
    return hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_payload(payload: Any, status_code: int) -> dict:
    """Preserve Graph error details and reject unsuccessful HTTP responses."""
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        if isinstance(err, dict):
            msg = err.get("message", "Unknown Threads API error")
            code = err.get("code", 0)
            subcode = err.get("error_subcode")
            detail = err.get("error_user_msg") or err.get("error_user_title")
            full = f"{msg} (code={code}" + (f", subcode={subcode})" if subcode else ")")
            if detail:
                full += f": {detail}"
            raise ThreadsAPIError(full, status_code=status_code, body=payload)
        raise ThreadsAPIError(str(err), status_code=status_code, body=payload)
    if not 200 <= status_code < 300:
        raise ThreadsAPIError(f"Threads API returned HTTP {status_code}.", status_code=status_code, body=payload)
    if not isinstance(payload, dict):
        raise ThreadsAPIError("Threads API response is not a JSON object.", status_code=status_code, body=payload)
    return payload


class ThreadsClient:
    """Async HTTP client wrapping the Threads Graph API."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        user_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.access_token = access_token
        self.user_id = user_id
        self.app_secret = app_secret
        self.timeout = timeout

    # ------------------------------------------------------------------ utils

    @property
    def configured(self) -> bool:
        return bool(self.access_token)

    def require_auth(self) -> None:
        if not self.configured:
            raise ThreadsAPIError(
                "No Threads access token configured. Set the THREADS_ACCESS_TOKEN "
                "environment variable (long-lived token from the Thread token "
                "exchange flow) or pass access_token to the tool call."
            )

    def require_user_id(self) -> str:
        self.require_auth()
        if not self.user_id:
            raise ThreadsAPIError(
                "No Threads user id configured. Set THREADS_USER_ID or call the "
                "threads_get_user_id tool with your access token."
            )
        return self.user_id

    def _params(self, extra: Optional[dict] = None) -> dict:
        params: dict[str, Any] = {"access_token": self.access_token}
        if self.app_secret and self.access_token:
            params["appsecret_proof"] = _appsecret_proof(
                self.app_secret, self.access_token
            )
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        return params

    # ------------------------------------------------------------- low level

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.request(method, url, **kwargs)
            except httpx.RequestError as e:
                # httpx errors can include the URL, which may carry credentials.
                raise ThreadsAPIError(f"Threads API request failed ({type(e).__name__}).") from None
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            raise ThreadsAPIError(
                f"Threads API returned invalid JSON (HTTP {resp.status_code}).",
                status_code=resp.status_code,
            ) from None
        return _check_payload(payload, resp.status_code)

    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        return await self._request("GET", path, params=params or {})

    async def post(self, path: str, data: Optional[dict] = None) -> dict:
        return await self._request("POST", path, data=data or {})

    async def delete(self, path: str, params: Optional[dict] = None) -> dict:
        return await self._request("DELETE", path, params=params or {})

    # -------------------------------------------------------------- identity

    async def lookup_user_id(self, username: str) -> dict:
        """Resolve a Threads profile id from a public username.

        GET /{username}/threads_profile_id?fields=threads_profile_id
        """
        return await self.get(
            f"{username}/threads_profile_id",
            params=self._params({"fields": "threads_profile_id"}),
        )

    async def get_user(self, user_id: Optional[str] = None, fields: str = "") -> dict:
        uid = user_id or self.require_user_id()
        return await self.get(uid, params=self._params({"fields": fields}))

    async def get_reply_management_settings(self) -> dict:
        uid = self.require_user_id()
        return await self.get(
            f"{uid}/reply_management_settings", params=self._params()
        )

    async def update_reply_management_settings(
        self, allow_replies: Optional[str] = None, allow_mentions: Optional[str] = None
    ) -> dict:
        uid = self.require_user_id()
        return await self.post(
            f"{uid}/reply_management_settings",
            data=self._params(
                {"allow_replies": allow_replies, "allow_mentions": allow_mentions}
            ),
        )

    # --------------------------------------------------------------- container

    async def create_container(
        self,
        is_carousel_item: bool = False,
        media_type: Optional[str] = None,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        text: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        link_attachment: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> dict:
        """Create a media container. media_type is required by the API."""
        uid = self.require_user_id()
        if not media_type:
            media_type = "VIDEO" if video_url else "IMAGE" if image_url else "TEXT"
        data: dict[str, Any] = {"media_type": media_type}
        if is_carousel_item:
            data["is_carousel_item"] = "true"
        if image_url:
            data["image_url"] = image_url
        if video_url:
            data["video_url"] = video_url
        if text is not None:
            data["text"] = text
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
        if link_attachment:
            data["link_attachment"] = link_attachment
        if location_id:
            data["location_id"] = location_id
        return await self.post(f"{uid}/threads", data=self._params(data))

    async def get_container_status(self, container_id: str) -> dict:
        return await self.get(
            container_id,
            params=self._params({"fields": "id,status,error_message"}),
        )

    async def wait_for_container(
        self, container_id: str, timeout: float = 300.0, interval: float = 5.0
    ) -> dict:
        """Poll a container until it reaches a terminal status.

        Terminal statuses are FINISHED, PUBLISHED, ERROR and EXPIRED. A carousel
        parent rejects children that are still processing ("invalid, do not
        exist, or have expired"), so each child must be waited on first.
        """
        deadline = time.monotonic() + timeout
        status: dict = {}
        while True:
            status = await self.get_container_status(container_id)
            if status.get("status") in ("FINISHED", "PUBLISHED", "ERROR", "EXPIRED"):
                break
            if time.monotonic() >= deadline:
                raise ThreadsAPIError(
                    f"Container {container_id} not ready after {timeout:.0f}s: "
                    f"{status}",
                    body=status,
                )
            await asyncio.sleep(interval)
        if status.get("status") in ("ERROR", "EXPIRED"):
            raise ThreadsAPIError(
                f"Container {container_id} failed: "
                f"{status.get('error_message') or status.get('status')}",
                body=status,
            )
        return status

    async def publish_container(self, creation_id: Optional[str] = None) -> dict:
        uid = self.require_user_id()
        cid = creation_id or ""
        if not cid:
            raise ThreadsAPIError("creation_id is required to publish a container.")
        return await self.post(
            f"{uid}/threads_publish", data=self._params({"creation_id": cid})
        )

    # -------------------------------------------------------------- publishing

    async def create_and_publish(
        self,
        text: Optional[str] = None,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        link_attachment: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> dict:
        """One-shot convenience: create a text/image/video container and publish it."""
        created = await self.create_container(
            image_url=image_url,
            video_url=video_url,
            text=text,
            reply_to_id=reply_to_id,
            link_attachment=link_attachment,
            location_id=location_id,
        )
        creation_id = created.get("id")
        if not creation_id:
            raise ThreadsAPIError(
                f"Container creation failed: {created}", body=created
            )
        published = await self.publish_container(creation_id)
        result: dict[str, Any] = {"creation_id": creation_id}
        result.update(published)
        return result

    async def create_carousel_container(
        self,
        children_ids: list[str],
        text: Optional[str] = None,
    ) -> dict:
        uid = self.require_user_id()
        data: dict[str, Any] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
        }
        if text is not None:
            data["text"] = text
        return await self.post(f"{uid}/threads", data=self._params(data))

    # ------------------------------------------------------------------- posts

    async def get_threads(
        self,
        fields: str = "id,permalink,text,timestamp,media_product_type,media_type,media_url,thumbnail_url,username,is_quote_post",
        limit: int = 25,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> dict:
        uid = self.require_user_id()
        params: dict[str, Any] = {"fields": fields, "limit": limit}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return await self.get(f"{uid}/threads", params=self._params(params))

    async def get_thread(self, thread_id: str, fields: str = "") -> dict:
        return await self.get(thread_id, params=self._params({"fields": fields}))

    async def get_children(self, thread_id: str, fields: str = "") -> dict:
        return await self.get(
            f"{thread_id}/children", params=self._params({"fields": fields})
        )

    async def get_replies(self, thread_id: str, fields: str = "") -> dict:
        return await self.get(
            f"{thread_id}/replies", params=self._params({"fields": fields})
        )

    async def delete_thread(self, thread_id: str) -> dict:
        return await self.delete(thread_id, params=self._params())

    # --------------------------------------------------------------- insights

    async def get_thread_insights(self, thread_id: str, metric: list[str]) -> dict:
        return await self.get(
            f"{thread_id}/insights",
            params=self._params({"metric": ",".join(metric)}),
        )

    async def get_user_insights(
        self,
        metric: list[str],
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> dict:
        uid = self.require_user_id()
        params: dict[str, Any] = {"metric": ",".join(metric)}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return await self.get(f"{uid}/threads_insights", params=self._params(params))
