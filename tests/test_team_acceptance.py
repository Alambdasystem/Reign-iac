"""Offline checks that the provider release receipt requires a real draft."""
from array import array
import io
import json
import math
import sys
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from reign.team_acceptance import run_acceptance


def audible_fixture():
    samples = array("h", (round(4000 * math.sin(2 * math.pi * 440 * index / 24000)) for index in range(12000)))
    if sys.byteorder != "little": samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(samples.tobytes())
    return output.getvalue()


class TeamAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, content):
        test = self
        caches = []
        class FixtureAssistant:
            def __init__(self, http, cache):
                self.cache = cache; caches.append(cache)
            async def __call__(self, context, actor, text, attachments, speak):
                test.assertTrue(speak); test.assertEqual(len(attachments), 1)
                picture = self.cache.get(attachments[0], context["room"]["id"], "image", actor["id"])
                with Image.open(io.BytesIO(picture["data"])) as image:
                    test.assertEqual((image.format, image.size), ("JPEG", (128, 128)))
                    red, green, blue = image.getpixel((64, 64))
                    test.assertGreater(blue, 200); test.assertLess(red + green, 30)
                sentence = "A blue square appears on a white background."
                key = self.cache.put(context["room"]["id"], "reign", audible_fixture(), "audio")
                return {"text": sentence, "segments": [{"text": sentence, "expression": "warm", "audio_id": key}],
                        "documents": [{"title": "Team release probe", "content": content}], "speech_status": "ready"}
        with patch("reign.team_acceptance.TeamAssistant", FixtureAssistant):
            receipt = await run_acceptance()
        self.assertTrue(caches); self.assertTrue(all(not cache.items for cache in caches))
        return receipt

    async def test_synthetic_image_audible_clip_and_nonempty_matching_draft_pass(self):
        receipt = await self.probe("# Team release probe\n\nA BLUE square appears on a white background.")
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(receipt["document_draft"])
        self.assertTrue(receipt["image_interpreted"])
        self.assertEqual(receipt["archive_writes"], 0)
        self.assertEqual(receipt["physical_device"], "not_run")
        self.assertEqual(receipt["audio"]["duration_seconds"], .5)
        self.assertGreater(receipt["audio"]["rms_normalized"], .002)

    async def test_empty_or_unrelated_draft_cannot_produce_a_passed_release_receipt(self):
        for content in ("", " \n\t", "A release document is ready.", "A blue circle.", "A red square.", "Blueberry squared."):
            with self.subTest(content=content):
                receipt = await self.probe(content)
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["stage"], "validation")
                self.assertEqual(receipt["error_code"], "team_provider_probe_failed")
                self.assertNotIn("document_draft", receipt)
                if content.strip(): self.assertNotIn(content, json.dumps(receipt))


if __name__ == "__main__": unittest.main()
