"""Thin async client for the Meta Threads API.

Endpoints implemented per:
https://developers.facebook.com/docs/threads/reference

All requests go through graph.threads.net using appsecret_proof when a
THREADS_APP_SECRET is configured (required by Meta for server-side calls).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Optional

import httpx

GRAPH_BASE = "https://graph.threads.net/v1.0"


class ThreadsAPIError(RuntimeError):
    """Raised when the Threads Graph API returns an error."""

    def __init__(self, message: str, status_code: int = 0, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _appsecret_proof(app_secret: str, access_token: str) -> str:
    return hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_payload(payload: dict) -> dict:
    """Raise a ThreadsAPIError if the Graph API payload contains an error."""
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        if isinstance(err, dict):
            msg = err.get("message", "Unknown Threads API error")
            code = err.get("code", 0)
            subcode = err.get("error_subcode")
            detail = err.get("error_user_msg") or err.get("error_user_title")
            full = f"{msg} (code={code}" + (f", subcode={subcode}" if subcode else "") + ")"
            if detail:
                full += f": {detail}"
            raise ThreadsAPIError(full, body=payload)
        raise ThreadsAPIError(str(err), body=payload)
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

    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params or {})
        payload = resp.json() if resp.content else {}
        _check_payload(payload)
        return payload

    async def post(self, path: str, data: Optional[dict] = None) -> dict:
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, data=data or {})
        payload = resp.json() if resp.content else {}
        _check_payload(payload)
        return payload

    async def delete(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(url, params=params or {})
        payload = resp.json() if resp.content else {}
        _check_payload(payload)
        return payload

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
        carousel_parent_id: Optional[str] = None,
        link_url: Optional[str] = None,
        location_id: Optional[str] = None,
        ast_disabling_keyword: Optional[str] = None,
        allow_comments: Optional[bool] = None,
    ) -> dict:
        uid = self.require_user_id()
        data: dict[str, Any] = {}
        if is_carousel_item:
            data["is_carousel_item"] = "true"
        elif media_type:
            data["media_type"] = media_type
        if image_url:
            data["image_url"] = image_url
        if video_url:
            data["video_url"] = video_url
        if text is not None:
            data["text"] = text
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
        if carousel_parent_id:
            data["carousel_parent_id"] = carousel_parent_id
        if link_url:
            data["link_url"] = link_url
        if location_id:
            data["location_id"] = location_id
        if ast_disabling_keyword:
            data["ast_disabling_keyword"] = ast_disabling_keyword
        if allow_comments is not None:
            data["allow_comments"] = "true" if allow_comments else "false"
        return await self.post(f"{uid}/threads_publishing", data=self._params(data))

    async def get_container_status(self, container_id: str) -> dict:
        return await self.get(
            container_id,
            params=self._params(
                {"fields": "id,status,error_message,is_finished,published_id"}
            ),
        )

    async def publish_container(self, creation_id: Optional[str] = None) -> dict:
        uid = self.require_user_id()
        cid = creation_id or ""
        if not cid:
            raise ThreadsAPIError("creation_id is required to publish a container.")
        return await self.post(
            f"{uid}/threads_publishing", data=self._params({"creation_id": cid})
        )

    # -------------------------------------------------------------- publishing

    async def create_and_publish(
        self,
        text: Optional[str] = None,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        link_url: Optional[str] = None,
        location_id: Optional[str] = None,
        allow_comments: Optional[bool] = None,
    ) -> dict:
        """One-shot convenience: create a text/image/video container and publish it."""
        media_type = None
        if video_url:
            media_type = "VIDEO"
        elif image_url:
            media_type = "IMAGE"
        created = await self.create_container(
            media_type=media_type,
            image_url=image_url,
            video_url=video_url,
            text=text,
            reply_to_id=reply_to_id,
            link_url=link_url,
            location_id=location_id,
            allow_comments=allow_comments,
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
        title: Optional[str] = None,
        text: Optional[str] = None,
    ) -> dict:
        uid = self.require_user_id()
        data: dict[str, Any] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
        }
        if title:
            data["title"] = title
        if text is not None:
            data["text"] = text
        return await self.post(f"{uid}/threads_publishing", data=self._params(data))

    # ------------------------------------------------------------------- posts

    async def get_threads(
        self,
        fields: str = "id,permalink,text,timestamp,media_product,type,media_url,thumbnail_url,username,is_quote_post",
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
