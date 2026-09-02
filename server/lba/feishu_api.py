"""Feishu Open Platform REST client (tenant token cache, bot DM, user OAuth, calendar read).

Only the endpoints the service needs. All calls go through one httpx.Client so tests can
inject a MockTransport. Never logs tokens or message bodies.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

OPEN_BASE = "https://open.feishu.cn"
ACCOUNTS_BASE = "https://accounts.feishu.cn"
READONLY_CALENDAR_SCOPES = ("offline_access", "calendar:calendar:readonly", "calendar:calendar.event:read")
ERR_NOT_IN_SCOPE = 230013  # bot has no availability to this user
ERR_USER_BLOCKED_BOT = 230053


class FeishuError(RuntimeError):
    def __init__(self, code: Any, message: str, *, http_status: int = 0) -> None:
        super().__init__(f"feishu error {code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status

    @property
    def is_auth_error(self) -> bool:
        """Token invalid/expired/revoked → caller should mark credential needs_reauth."""
        return str(self.code) in {"20001", "20003", "20005", "20026", "99991661", "99991663", "99991668", "invalid_grant", "invalid_token"}


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, *, http: Optional[httpx.Client] = None, timeout: float = 20.0) -> None:
        self.app_id = app_id
        self._app_secret = app_secret
        self._http = http or httpx.Client(timeout=timeout)
        self._tenant_token = ""
        self._tenant_expires = 0.0
        self._lock = threading.Lock()

    # --- low level ----------------------------------------------------------
    def _request(self, method: str, url: str, *, token: Optional[str] = None, params: Optional[Dict[str, Any]] = None,
                 json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._http.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise FeishuError("network", type(exc).__name__) from exc
        try:
            payload = response.json()
        except ValueError:
            raise FeishuError("bad_json", f"HTTP {response.status_code}", http_status=response.status_code)
        if not isinstance(payload, dict):
            raise FeishuError("bad_json", "non-object response", http_status=response.status_code)
        code = payload.get("code", 0 if response.status_code < 400 else response.status_code)
        if code not in (0, "0", None) or response.status_code >= 400:
            message = payload.get("msg") or payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
            raise FeishuError(payload.get("error") or code, str(message), http_status=response.status_code)
        return payload

    # --- tenant identity ----------------------------------------------------
    def tenant_token(self) -> str:
        with self._lock:
            if self._tenant_token and time.time() < self._tenant_expires - 60:
                return self._tenant_token
            payload = self._request("POST", f"{OPEN_BASE}/open-apis/auth/v3/tenant_access_token/internal",
                                    json_body={"app_id": self.app_id, "app_secret": self._app_secret})
            self._tenant_token = payload["tenant_access_token"]
            self._tenant_expires = time.time() + float(payload.get("expire", 7200))
            return self._tenant_token

    def send_text(self, open_id: str, text: str) -> str:
        return self._send(open_id, "text", {"text": text})

    def send_card(self, open_id: str, card: Dict[str, Any]) -> str:
        return self._send(open_id, "interactive", card)

    def _send(self, open_id: str, msg_type: str, content: Dict[str, Any]) -> str:
        payload = self._request(
            "POST", f"{OPEN_BASE}/open-apis/im/v1/messages", token=self.tenant_token(),
            params={"receive_id_type": "open_id"},
            json_body={"receive_id": open_id, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
        )
        return str((payload.get("data") or {}).get("message_id", ""))

    # --- user OAuth ---------------------------------------------------------
    def authorize_url(self, redirect_uri: str, state: str, scopes: List[str] = list(READONLY_CALENDAR_SCOPES)) -> str:
        query = urlencode({"client_id": self.app_id, "redirect_uri": redirect_uri, "scope": " ".join(scopes), "state": state})
        return f"{ACCOUNTS_BASE}/open-apis/authen/v1/authorize?{query}"

    def oauth_exchange(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        return self._token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})

    def oauth_refresh(self, refresh_token: str) -> Dict[str, Any]:
        return self._token_call({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def _token_call(self, body: Dict[str, Any]) -> Dict[str, Any]:
        body = {"client_id": self.app_id, "client_secret": self._app_secret, **body}
        payload = self._request("POST", f"{OPEN_BASE}/open-apis/authen/v2/oauth/token", json_body=body)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if "access_token" not in data:
            raise FeishuError("bad_token_response", "no access_token in response")
        now = time.time()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "scope": data.get("scope", ""),
            "access_expires_at": now + float(data.get("expires_in", 7200)),
            "refresh_expires_at": now + float(data.get("refresh_token_expires_in", 0)) if data.get("refresh_token_expires_in") else None,
        }

    def user_info(self, user_token: str) -> Dict[str, Any]:
        payload = self._request("GET", f"{OPEN_BASE}/open-apis/authen/v1/user_info", token=user_token)
        return payload.get("data") or {}

    # --- calendar (user identity) ------------------------------------------
    def primary_calendar_id(self, user_token: str) -> str:
        payload = self._request("POST", f"{OPEN_BASE}/open-apis/calendar/v4/calendars/primary", token=user_token)
        calendars = (payload.get("data") or {}).get("calendars") or []
        for item in calendars:
            calendar = item.get("calendar") or {}
            if calendar.get("calendar_id"):
                return str(calendar["calendar_id"])
        raise FeishuError("no_primary_calendar", "primary calendar not found")

    def calendar_instances(self, user_token: str, calendar_id: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
        """Expanded (recurrence-aware) events in [start_ts, end_ts]. Returns trimmed dicts only."""
        payload = self._request(
            "GET", f"{OPEN_BASE}/open-apis/calendar/v4/calendars/{calendar_id}/events/instance_view",
            token=user_token, params={"start_time": str(start_ts), "end_time": str(end_ts)},
        )
        items = (payload.get("data") or {}).get("items") or []
        events: List[Dict[str, Any]] = []
        for item in items:
            if item.get("status") == "cancelled":
                continue
            events.append({
                "summary": (item.get("summary") or "").strip()[:200],
                "start": (item.get("start_time") or {}).get("timestamp"),
                "end": (item.get("end_time") or {}).get("timestamp"),
                "description": (item.get("description") or "").strip()[:500],
                "attendee_count": len(item.get("attendees") or []),
                "is_all_day": bool((item.get("start_time") or {}).get("date")),
            })
        return events
