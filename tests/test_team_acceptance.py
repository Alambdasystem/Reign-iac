"""Offline checks that the provider release receipt requires a real draft."""
from array import array
import io
import json
import math
import sys
import unittest
from unittest.mock import AsyncMock, patch
import wave

from PIL import Image

from reign.team_acceptance import EXPRESSION_FIXTURE, run_acceptance


def audible_fixture():
    samples = array("h", (round(4000 * math.sin(2 * math.pi * 440 * index / 24000)) for index in range(12000)))
    if sys.byteorder != "little": samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(samples.tobytes())
    return output.getvalue()


class TeamAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, content, expression_segments=None, expression_error=None, provenance_text="Spoken."):
        test = self
        caches = []
        class FixtureAssistant:
            def __init__(self, http, cache):
                self.cache = cache; caches.append(cache)
            async def __call__(self, context, actor, text, attachments, speak):
                if not speak:
                    test.assertEqual(context["input_source"], "voice")
                    test.assertEqual(attachments, [])
                    return {"text": provenance_text, "segments": [{"text": provenance_text, "expression": "neutral"}],
                            "documents": [], "speech_status": "not_requested"}
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
        expression_segments = expression_segments if expression_segments is not None else [
            {"text": "The draft is saved.", "expression": "warm"},
            {"text": "Please review it before sharing.", "expression": "thoughtful"}]
        with patch("reign.team_acceptance.TeamAssistant", FixtureAssistant), \
             patch("reign.team_acceptance.PrivateSpeech.annotate", new_callable=AsyncMock,
                   return_value=expression_segments, side_effect=expression_error) as annotations:
            receipt = await run_acceptance()
            if receipt["status"] == "passed": annotations.assert_awaited_once_with(EXPRESSION_FIXTURE)
        self.assertTrue(caches); self.assertTrue(all(not cache.items for cache in caches))
        return receipt

    async def test_synthetic_image_audible_clip_and_nonempty_matching_draft_pass(self):
        receipt = await self.probe("# Team release probe\n\nA BLUE square appears on a white background.")
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(receipt["document_draft"])
        self.assertTrue(receipt["image_interpreted"])
        self.assertTrue(receipt["private_expression_annotation"])
        self.assertTrue(receipt["spoken_input_provenance"])
        self.assertEqual(receipt["archive_writes"], 0)
        self.assertEqual(receipt["physical_device"], "not_run")
        self.assertEqual(receipt["audio"]["duration_seconds"], .5)
        self.assertGreater(receipt["audio"]["rms_normalized"], .002)

    async def test_spoken_metadata_misread_as_typing_cannot_pass_provider_gate(self):
        receipt = await self.probe("A blue square.", provenance_text="Typed.")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["stage"], "voice_provenance")
        self.assertNotIn("spoken_input_provenance", receipt)

    async def test_empty_or_unrelated_draft_cannot_produce_a_passed_release_receipt(self):
        for content in ("", " \n\t", "A release document is ready.", "A blue circle.", "A red square.", "Blueberry squared."):
            with self.subTest(content=content):
                receipt = await self.probe(content)
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["stage"], "validation")
                self.assertEqual(receipt["error_code"], "team_provider_probe_failed")
                self.assertNotIn("document_draft", receipt)
                if content.strip(): self.assertNotIn(content, json.dumps(receipt))

    async def test_expression_failure_or_changed_words_cannot_pass_the_live_provider_gate(self):
        for segments, error in (
            ([{"text": "The draft is shared.", "expression": "warm"}], None),
            ([{"text": EXPRESSION_FIXTURE, "expression": "invented-mood"}], None),
            ([], None),
            (None, TimeoutError("private-provider-details")),
        ):
            with self.subTest(segments=segments, error=type(error).__name__):
                receipt = await self.probe("A blue square.", segments, error)
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["stage"], "expressions")
                self.assertNotIn("private_expression_annotation", receipt)
                self.assertNotIn("private-provider-details", json.dumps(receipt))


if __name__ == "__main__": unittest.main()
