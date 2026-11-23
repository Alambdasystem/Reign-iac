"""Unit tests: no live LinkedIn access or credentials."""
import os
import unittest
from unittest.mock import patch, AsyncMock
from reign.linkedin_publishing import preview, publish


class LinkedinPublishingTests(unittest.IsolatedAsyncioTestCase):
    def test_preview_has_no_side_effects(self):
        result = preview("A safe preview")
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["media_supported"])
        self.assertNotIn("token", result)

    async def test_author_change_refuses_publish(self):
        with patch.dict(os.environ, {"REIGN_LINKEDIN_PUBLISH_ENABLED": "true",
                                     "REIGN_LINKEDIN_AUTHOR_URN": "urn:li:person:abc123",
                                     "REIGN_LINKEDIN_ACCESS_TOKEN": "test-token"}):
            response = await publish("Post", expected_author="urn:li:person:another")
            self.assertEqual(response["code"], "author_changed")

    async def test_off_by_default(self):
        with patch.dict(os.environ, {"REIGN_LINKEDIN_PUBLISH_ENABLED": "false",
                                     "REIGN_LINKEDIN_AUTHOR_URN": "urn:li:person:abc123",
                                     "REIGN_LINKEDIN_ACCESS_TOKEN": "test-token"}):
            self.assertEqual((await publish("Post", expected_author="urn:li:person:abc123"))["code"], "linkedin_unavailable")

    async def test_success_uses_exact_configured_author(self):
        response = type("Response", (), {"status_code": 201,
                     "headers": {"x-restli-id": "urn:li:share:123456789"}})()
        with patch.dict(os.environ, {"REIGN_LINKEDIN_PUBLISH_ENABLED": "true",
                                     "REIGN_LINKEDIN_AUTHOR_URN": "urn:li:person:abc123",
                                     "REIGN_LINKEDIN_ACCESS_TOKEN": "test-token"}):
            with patch("reign.linkedin_publishing.httpx.AsyncClient") as client:
                client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
                result = await publish("Hello", expected_author="urn:li:person:abc123")
                self.assertEqual(result["status"], "published")
                payload = client.return_value.__aenter__.return_value.post.call_args.kwargs["json"]
                self.assertEqual(payload["author"], "urn:li:person:abc123")

    async def test_uncertain_success_does_not_fabricate_id(self):
        response = type("Response", (), {"status_code": 201, "headers": {}})()
        with patch.dict(os.environ, {"REIGN_LINKEDIN_PUBLISH_ENABLED": "true",
                                     "REIGN_LINKEDIN_AUTHOR_URN": "urn:li:person:abc123",
                                     "REIGN_LINKEDIN_ACCESS_TOKEN": "test-token"}):
            with patch("reign.linkedin_publishing.httpx.AsyncClient") as client:
                client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
                self.assertEqual((await publish("Hello", expected_author="urn:li:person:abc123"))["code"], "publish_uncertain")


if __name__ == "__main__":
    unittest.main()
