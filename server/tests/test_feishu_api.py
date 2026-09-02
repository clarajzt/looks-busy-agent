import json
import unittest

import httpx

from lba.feishu_api import FeishuClient, FeishuError

from . import helpers  # noqa: F401  (sys.path setup)


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return FeishuClient("cli_test", "secret", http=httpx.Client(transport=transport))


class FeishuApiTests(unittest.TestCase):
    def test_tenant_token_cached_and_dm_payload(self):
        calls = {"token": 0, "sent": []}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/tenant_access_token/internal"):
                calls["token"] += 1
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-abc", "expire": 7200})
            if request.url.path.endswith("/im/v1/messages"):
                self.assertEqual(request.url.params["receive_id_type"], "open_id")
                self.assertEqual(request.headers["Authorization"], "Bearer t-abc")
                calls["sent"].append(json.loads(request.content))
                return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})
            return httpx.Response(404, json={"code": 404, "msg": "nope"})

        client = make_client(handler)
        self.assertEqual(client.send_text("ou_x", "hi"), "om_1")
        self.assertEqual(client.send_text("ou_x", "again"), "om_1")
        self.assertEqual(calls["token"], 1, "tenant token must be cached")
        body = calls["sent"][0]
        self.assertEqual(body["receive_id"], "ou_x")
        self.assertEqual(json.loads(body["content"]), {"text": "hi"})

    def test_availability_error_surfaces_code(self):
        def handler(request):
            if "tenant_access_token" in request.url.path:
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t", "expire": 100})
            return httpx.Response(200, json={"code": 230013, "msg": "Bot has NO availability to this user"})

        with self.assertRaises(FeishuError) as ctx:
            make_client(handler).send_text("ou_y", "x")
        self.assertEqual(ctx.exception.code, 230013)

    def test_oauth_exchange_and_refresh(self):
        def handler(request):
            body = json.loads(request.content)
            if body.get("grant_type") == "authorization_code":
                self.assertEqual(body["code"], "c1")
                return httpx.Response(200, json={"code": 0, "access_token": "u-a", "refresh_token": "u-r", "expires_in": 7200,
                                                 "refresh_token_expires_in": 604800, "scope": "offline_access calendar:calendar:readonly"})
            if body.get("grant_type") == "refresh_token":
                return httpx.Response(400, json={"code": 20026, "error": "invalid_grant", "error_description": "refresh token expired"})
            return httpx.Response(404)

        client = make_client(handler)
        grant = client.oauth_exchange("c1", "https://x/oauth/callback")
        self.assertEqual(grant["access_token"], "u-a")
        self.assertEqual(grant["refresh_token"], "u-r")
        self.assertTrue(grant["refresh_expires_at"] > grant["access_expires_at"])
        with self.assertRaises(FeishuError) as ctx:
            client.oauth_refresh("u-r")
        self.assertTrue(ctx.exception.is_auth_error)

    def test_authorize_url_requests_readonly_scopes(self):
        url = make_client(lambda r: httpx.Response(404)).authorize_url("https://x/oauth/callback", "nonce.sig")
        self.assertIn("accounts.feishu.cn/open-apis/authen/v1/authorize", url)
        self.assertIn("offline_access", url)
        self.assertIn("calendar%3Acalendar%3Areadonly", url)
        self.assertNotIn("calendar.event%3Acreate", url)

    def test_calendar_instances_trims_and_skips_cancelled(self):
        def handler(request):
            self.assertTrue(request.url.path.endswith("/events/instance_view"))
            return httpx.Response(200, json={"code": 0, "data": {"items": [
                {"summary": "周会", "start_time": {"timestamp": "1"}, "end_time": {"timestamp": "2"}, "attendees": [{}, {}], "description": "x" * 900},
                {"summary": "取消的", "status": "cancelled"},
            ]}})

        events = make_client(handler).calendar_instances("u-a", "cal", 0, 10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["attendee_count"], 2)
        self.assertEqual(len(events[0]["description"]), 500)


if __name__ == "__main__":
    unittest.main()
