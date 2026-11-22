"""Fake-provider and real-media checks for the isolated shared room assistant."""
import base64
from datetime import datetime, timezone
import io
import json
import os
import tempfile
import unittest
import uuid
import wave
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
from PIL import Image, PngImagePlugin

from reign.team_api import TeamRuntime, install as install_team
from reign.team_assistant import Narrator, PinnedConnect, TeamAssistant, normalize_speech
from reign.team_identity import ACTOR_HEADER, issue_actor
from reign.team_media import TeamMedia, TeamMediaError, clean_image, install as install_media
from reign.team_store import TeamError, TeamStore, owner_identity

TOKEN = "synthetic-team-media-core-token-for-tests-12345678"
PROVIDER = {"key": "synthetic-provider-key-for-tests", "base": "https://provider.example/v1", "model": "gpt-realtime-test"}


def data_image(size=(128, 128)):
    image = Image.new("RGB", size, (80, 125, 170))
    metadata = PngImagePlugin.PngInfo(); metadata.add_text("private", "private-fixture-image-metadata")
    result = io.BytesIO(); image.save(result, format="PNG", pnginfo=metadata)
    return "data:image/png;base64," + base64.b64encode(result.getvalue()).decode()


def wav_clip():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(bytes(12000))
    return output.getvalue()


def provider_response(segments=None, **extra):
    value = {"segments": segments or [{"text": "Here is the room's answer.", "expression": "warm"}],
             "documents": [{"title": "Shared note", "content": "A complete saved document."}]}
    return httpx.Response(200, json={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}], **extra})


class MediaStoreTests(unittest.TestCase):
    def test_image_is_decoded_reencoded_bounded_and_metadata_free(self):
        result = clean_image(data_image((1200, 800)))
        self.assertNotIn(b"private-fixture-image-metadata", result)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(image.width, 960); self.assertLessEqual(image.height, 540)
            self.assertEqual(image.mode, "RGB")
        for value in ("data:image/png;base64,not-base64", "https://private.example/image", data_image((1600, 1600))):
            with self.assertRaises(TeamMediaError): clean_image(value)

    def test_receipts_are_room_user_kind_and_expiry_bound(self):
        now = [1000.0]; media = TeamMedia(clock=lambda: now[0])
        room, other_room, user, other_user = (str(uuid.uuid4()) for _ in range(4))
        image = media.put(room, user, b"sanitized-image-fixture", "image")
        audio = media.put(room, "reign", wav_clip(), "audio")
        self.assertEqual(media.get(image, room, "image", user)["user_id"], user)
        for arguments in ((image, other_room, "image", user), (image, room, "image", other_user), (image, room, "audio")):
            with self.assertRaises(TeamMediaError) as error: media.get(*arguments)
            self.assertEqual(error.exception.code, "media_expired")
        now[0] += 301
        with self.assertRaises(TeamMediaError): media.get(image, room, "image", user)
        self.assertTrue(media.get(audio, room, "audio"))
        now[0] += 300
        with self.assertRaises(TeamMediaError): media.get(audio, room, "audio")
        self.assertEqual(media.items, {})


class MediaAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-team-media-"); self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"CORE_TOKEN": TOKEN}); self.env.start(); self.addCleanup(self.env.stop)
        self.store = TeamStore(self.temp.name, bytes(range(32))); self.store.initialize()
        self.owner = owner_identity()
        created = self.store.create_member(self.owner, "media-employee", "Media Employee")
        self.member = self.store.resolve(created["member"]["id"], 1)
        self.room = self.store.create_room(self.owner, "Media room")["room"]["id"]
        self.other = self.store.create_room(self.owner, "Different room")["room"]["id"]
        self.runtime = TeamRuntime(self.store); self.media = TeamMedia()
        app = FastAPI(); app.state.team = self.runtime; app.state.team_media = self.media
        app.state.http = AsyncMock(); self.http = app.state.http
        def authorize(request):
            if request.headers.get("authorization") != "Bearer " + TOKEN: raise HTTPException(401, "Unauthorized")
        install_team(app, authorize); install_media(app)
        self.client = TestClient(app); self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)
        self.configuration = patch("reign.team_media.configuration", return_value=PROVIDER)
        self.configuration.start(); self.addCleanup(self.configuration.stop)

    def headers(self, actor=None):
        actor = actor or self.member
        return {"Authorization": "Bearer " + TOKEN, ACTOR_HEADER: issue_actor(actor["id"], actor["role"], actor["credential_revision"])}

    def path(self, suffix): return f"/api/team/rooms/{self.room}/{suffix}"

    def payload(self, cid):
        return {"data_url": data_image(), "source": "camera", "connection_id": cid,
                "captured_at": datetime.now(timezone.utc).isoformat()}

    def test_media_routes_require_service_and_live_actor_before_processing(self):
        for method, path in (("POST", self.path("attachments")), ("POST", self.path("transcriptions")),
                             ("GET", self.path("audio/" + str(uuid.uuid4())))):
            for headers in ({}, {"Authorization": "Bearer " + TOKEN}):
                response = self.client.request(method, path, headers=headers, json={"private": "never-reflect-fixture"})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("never-reflect-fixture", response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
        self.http.post.assert_not_awaited()

    def test_attachment_requires_current_owned_call_track_and_rechecks_after_decode(self):
        cid = str(uuid.uuid4()); payload = self.payload(cid)
        self.assertEqual(self.client.post(self.path("attachments"), headers=self.headers(), json=payload).status_code, 403)
        self.client.post(self.path("presence"), headers=self.headers(), json={"connection_id": cid, "in_call": True})
        self.assertEqual(self.client.post(self.path("attachments"), headers=self.headers(), json=payload).status_code, 422)
        self.client.post(self.path("presence"), headers=self.headers(), json={"connection_id": cid, "in_call": True, "camera": True})
        response = self.client.post(self.path("attachments"), headers=self.headers(), json=payload)
        self.assertEqual(response.status_code, 200)
        key = response.json()["attachment"]["id"]
        saved = self.media.get(key, self.room, "image", self.member["id"])
        self.assertEqual(saved["data"], clean_image(payload["data_url"]))
        self.assertNotIn("data_url", response.text)
        for bad in ({**payload, "source": {}}, {**payload, "captured_at": "2000-01-01T00:00:00Z"}):
            self.assertEqual(self.client.post(self.path("attachments"), headers=self.headers(), json=bad).status_code, 422)
        self.media.pending_images = 2
        with patch("reign.team_media.clean_image") as decoder:
            self.assertEqual(self.client.post(self.path("attachments"), headers=self.headers(), json=payload).status_code, 429)
            decoder.assert_not_called()
        self.media.pending_images = 0
        original = clean_image
        def leave_during_decode(value):
            self.runtime.presence.pop(cid, None)
            return original(value)
        with patch("reign.team_media.clean_image", side_effect=leave_during_decode):
            response = self.client.post(self.path("attachments"), headers=self.headers(), json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.media.items), 1)

    def test_shared_audio_is_room_bound_and_membership_is_rechecked(self):
        key = self.media.put(self.room, "reign", wav_clip(), "audio")
        response = self.client.get(self.path("audio/" + key), headers=self.headers())
        self.assertEqual(response.status_code, 200); self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get(f"/api/team/rooms/{self.other}/audio/{key}", headers=self.headers()).status_code, 410)
        self.store.disable_member(self.owner, self.member["id"])
        self.assertEqual(self.client.get(self.path("audio/" + key), headers=self.headers()).status_code, 401)

    def test_member_revoked_during_pending_clip_wait_never_receives_audio(self):
        key = self.media.put(self.room, "reign", wav_clip(), "audio")
        async def finished_after_revocation(audio_id, room_id):
            self.store.disable_member(self.owner, self.member["id"])
            return wav_clip()
        with patch.object(self.media, "audio_bytes", side_effect=finished_after_revocation):
            response = self.client.get(self.path("audio/" + key), headers=self.headers())
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b"RIFF", response.content)

    def test_local_camera_preview_uses_owned_viewer_lease_without_joining_or_sharing_a_call(self):
        cid = str(uuid.uuid4())
        self.client.post(self.path("presence"), headers=self.headers(), json={"connection_id": cid})
        payload = {**self.payload(cid), "source": "camera_preview"}
        response = self.client.post(self.path("attachments"), headers=self.headers(), json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()["attachment"]
        self.assertEqual(receipt["source"], "camera_preview")
        self.assertEqual(receipt["participant_id"], self.member["id"])
        stored = self.media.get(receipt["id"], self.room, "image", self.member["id"])
        self.assertEqual(stored["data"], clean_image(payload["data_url"]))
        self.assertNotIn("private-fixture-image-metadata", response.text)
        self.assertTrue(all(not self.runtime.presence[cid][flag] for flag in ("in_call", "camera", "microphone", "screen")))
        # The new local still purpose cannot weaken shared-call track checks.
        for source in ("camera", "screen"):
            result = self.client.post(self.path("attachments"), headers=self.headers(), json={**payload, "source": source})
            self.assertEqual(result.status_code, 403)

    def test_local_preview_cannot_use_another_person_room_call_or_expired_connection(self):
        own, foreign, other_room, call = (str(uuid.uuid4()) for _ in range(4))
        self.client.post(self.path("presence"), headers=self.headers(), json={"connection_id": own})
        self.client.post(self.path("presence"), headers=self.headers(self.owner), json={"connection_id": foreign})
        self.client.post(f"/api/team/rooms/{self.other}/presence", headers=self.headers(), json={"connection_id": other_room})
        self.client.post(self.path("presence"), headers=self.headers(), json={"connection_id": call, "in_call": True, "camera": True})
        self.runtime.presence[own]["expires_at"] = self.runtime.clock() - 1
        with patch("reign.team_media.clean_image") as decoder:
            for cid in (own, foreign, other_room, call, str(uuid.uuid4())):
                with self.subTest(connection=cid):
                    response = self.client.post(self.path("attachments"), headers=self.headers(),
                        json={**self.payload(cid), "source": "camera_preview"})
                    self.assertEqual(response.status_code, 403)
            decoder.assert_not_called()
        self.assertEqual(self.media.items, {})

    def test_local_preview_rechecks_room_leave_and_membership_after_decoding(self):
        original = clean_image
        for action in ("leave", "revoke"):
            cid = str(uuid.uuid4())
            self.client.post(self.path("presence"), headers=self.headers(), json={"connection_id": cid})
            def change_access(value):
                if action == "leave": self.runtime.presence.pop(cid, None)
                else: self.store.disable_member(self.owner, self.member["id"])
                return original(value)
            with self.subTest(action=action), patch("reign.team_media.clean_image", side_effect=change_access):
                response = self.client.post(self.path("attachments"), headers=self.headers(),
                    json={**self.payload(cid), "source": "camera_preview"})
                self.assertEqual(response.status_code, 403 if action == "leave" else 401)
            self.assertEqual(self.media.items, {})
            self.assertEqual(self.media.pending_images, 0)

    def test_transcription_is_bounded_pinned_and_sanitizes_invalid_provider_responses(self):
        payload = {"mime_type": "audio/wav", "audio_base64": base64.b64encode(wav_clip()).decode()}
        self.http.post.return_value = httpx.Response(200, json={"text": "  A dictated room message.  "})
        response = self.client.post(self.path("transcriptions"), headers=self.headers(), json=payload)
        self.assertEqual(response.json()["text"], "A dictated room message.")
        key = response.json()["transcription_id"]
        self.media.validate_transcription(self.member, self.room, key, "A dictated room message.")
        args = self.http.post.await_args
        self.assertEqual(args.args[0], PROVIDER["base"] + "/audio/transcriptions")
        self.assertFalse(args.kwargs["follow_redirects"])
        self.assertEqual(args.kwargs["files"]["file"][1], wav_clip())
        for upstream in (httpx.Response(302, json={"text": "private-provider-fixture"}, headers={"Location": "https://elsewhere.example"}),
                         httpx.Response(200, json={"text": ["private-provider-fixture"]}),
                         httpx.Response(401, json={"error": "private-provider-fixture"})):
            self.http.post.return_value = upstream
            response = self.client.post(self.path("transcriptions"), headers=self.headers(), json=payload)
            self.assertEqual(response.status_code, 503); self.assertNotIn("private-provider-fixture", response.text)
            self.assertEqual(self.media.pending_transcriptions, 0)
        self.media.pending_transcriptions = 2
        before = self.http.post.await_count
        self.assertEqual(self.client.post(self.path("transcriptions"), headers=self.headers(), json=payload).status_code, 429)
        self.assertEqual(self.http.post.await_count, before)
        self.media.pending_transcriptions = 0


class AssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.http = AsyncMock(); self.http.post.return_value = provider_response()
        self.media = TeamMedia(); self.narrator = AsyncMock(); self.narrator.generate.return_value = [wav_clip()]
        self.actor = {"id": str(uuid.uuid4()), "display_name": "Fixture Employee", "role": "employee", "credential_revision": 1}
        self.room = str(uuid.uuid4())
        self.context = {"room": {"id": self.room, "title": "Authorized fixture room"},
                        "messages": [{"role": "user", "text": "Room-only discussion"}], "documents": []}
        self.assistant = TeamAssistant(self.http, self.media, self.narrator)
        self.config = patch("reign.team_assistant.configuration", return_value=PROVIDER)
        self.config.start(); self.addCleanup(self.config.stop)

    async def test_provider_receives_actual_sanitized_pixels_and_only_given_room_context(self):
        clean = clean_image(data_image()); key = self.media.put(self.room, self.actor["id"], clean, "image", {"source": "camera"})
        result = await self.assistant(self.context, self.actor, "Explain this shared image.", [key], False)
        args = self.http.post.await_args
        self.assertEqual(args.args[0], PROVIDER["base"] + "/responses")
        self.assertFalse(args.kwargs["follow_redirects"])
        payload = args.kwargs["json"]
        self.assertFalse(payload["store"])
        content = payload["input"][0]["content"]
        image = next(part for part in content if part["type"] == "input_image")
        self.assertEqual(base64.b64decode(image["image_url"].split(",", 1)[1]), clean)
        supplied = json.loads(content[0]["text"])
        self.assertEqual(supplied["prior_messages"], self.context["messages"])
        self.assertEqual(supplied["participants_requesting"]["id"], self.actor["id"])
        self.assertNotIn("conversation", payload)
        self.assertEqual(result["speech_status"], "not_requested"); self.narrator.generate.assert_not_awaited()

    async def test_wrong_room_or_other_participant_receipts_never_reach_provider(self):
        for room, user in ((str(uuid.uuid4()), self.actor["id"]), (self.room, str(uuid.uuid4()))):
            key = self.media.put(room, user, clean_image(data_image()), "image")
            with self.assertRaises(TeamMediaError): await self.assistant(self.context, self.actor, "Look", [key], False)
        self.http.post.assert_not_awaited()

    async def test_local_camera_description_uses_actual_receipt_pixels_and_narrates_the_same_answer(self):
        clean = clean_image(data_image())
        metadata = {"source": "camera_preview", "participant_id": self.actor["id"],
                    "captured_at": datetime.now(timezone.utc).isoformat()}
        key = self.media.put(self.room, self.actor["id"], clean, "image", metadata)
        result = await self.assistant(self.context, self.actor,
            "Describe what you can see in this camera frame aloud.", [key], True)
        payload = self.http.post.await_args.kwargs["json"]
        content = payload["input"][0]["content"]
        images = [part for part in content if part["type"] == "input_image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(base64.b64decode(images[0]["image_url"].split(",", 1)[1]), clean)
        self.assertIn("camera_preview", json.dumps(content))
        self.assertIn("not the identity of anyone pictured", payload["instructions"])
        self.assertIn("Do not fill in unseen details", payload["instructions"])
        self.assertIn("not a group-call broadcast or live feed", payload["instructions"])
        self.assertEqual(result["speech_status"], "ready")
        spoken = self.narrator.generate.await_args.args[0]
        self.assertEqual(" ".join(segment["text"] for segment in spoken), result["text"])
        audio = self.media.get(result["segments"][0]["audio_id"], self.room, "audio")
        self.assertEqual(audio["data"], wav_clip())

    async def test_narration_gets_exact_sentences_and_room_bound_segment_audio_receipts(self):
        segments = [{"text": "First sentence.", "expression": "warm"}, {"text": "Second sentence.", "expression": "thoughtful"}]
        self.http.post.return_value = provider_response(segments)
        self.narrator.generate.return_value = [wav_clip(), wav_clip()]
        result = await self.assistant(self.context, self.actor, "Give a reply.", [], True)
        self.assertEqual(result["speech_status"], "ready")
        self.assertEqual(result["text"], "First sentence. Second sentence.")
        self.assertEqual([part["text"] for part in self.narrator.generate.await_args.args[0]], [part["text"] for part in segments])
        ids = [part["audio_id"] for part in result["segments"]]
        self.assertEqual(len(set(ids)), 2)
        for key in ids: self.assertEqual(self.media.get(key, self.room, "audio")["data"], wav_clip())

    async def test_speech_failure_preserves_answer_and_documents_and_cleans_partial_audio(self):
        self.narrator.generate.side_effect = RuntimeError("private-provider-key-fixture")
        result = await self.assistant(self.context, self.actor, "Make a note.", [], True)
        self.assertEqual(result["speech_status"], "unavailable")
        self.assertTrue(result["text"]); self.assertEqual(result["documents"][0]["title"], "Shared note")
        self.assertNotIn("private-provider-key-fixture", json.dumps(result))
        segments = [{"text": "One.", "expression": "neutral"}, {"text": "Two.", "expression": "neutral"}]
        self.http.post.return_value = provider_response(segments)
        self.narrator.generate.side_effect = None; self.narrator.generate.return_value = [wav_clip(), wav_clip()]
        with patch("reign.team_media.MAX_MEDIA_BYTES", len(wav_clip())):
            result = await self.assistant(self.context, self.actor, "Make a note.", [], True)
        self.assertEqual(result["speech_status"], "unavailable")
        self.assertTrue(all("audio_id" not in segment for segment in result["segments"]))
        self.assertEqual(self.media.items, {})

    async def test_redirect_incomplete_response_and_refusal_are_rejected(self):
        refused = provider_response().json()
        refused["output"][0]["content"].append({"type": "refusal", "refusal": "private-provider-refusal-fixture"})
        for upstream in (httpx.Response(302, headers={"Location": "https://elsewhere.example"}),
                         provider_response(status="incomplete"), httpx.Response(200, json=refused)):
            self.http.post.return_value = upstream
            with self.assertRaises(TeamError) as error: await self.assistant(self.context, self.actor, "Hello", [], True)
            self.assertEqual(error.exception.code, "assistant_failed")
            self.assertNotIn("private-provider-refusal-fixture", str(error.exception))
        self.narrator.generate.assert_not_awaited()


class FakeSocket:
    def __init__(self, events): self.events = list(events); self.sent = []
    async def recv(self): return json.dumps(self.events.pop(0))
    async def send(self, message): self.sent.append(json.loads(message))
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


class NarratorTests(unittest.IsolatedAsyncioTestCase):
    def events(self, transcript):
        return [{"type": "session.created"}, {"type": "session.updated"},
                {"type": "response.output_audio.delta", "delta": base64.b64encode(bytes(12000)).decode()},
                {"type": "response.output_audio_transcript.done", "transcript": transcript},
                {"type": "response.done", "response": {"status": "completed"}}]

    async def test_exact_narration_has_no_tools_and_emits_real_pcm_wav(self):
        socket = FakeSocket(self.events("A clear sentence.")); calls = []
        def connection(*args, **kwargs): calls.append((args, kwargs)); return socket
        with patch("reign.team_assistant.configuration", return_value=PROVIDER):
            clips = await Narrator(connection).generate([{"text": "A clear sentence.", "expression": "warm"}])
        with wave.open(io.BytesIO(clips[0]), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()), (1, 2, 24000, 6000))
        self.assertEqual(socket.sent[0]["session"]["tools"], [])
        self.assertEqual(socket.sent[0]["session"]["tool_choice"], "none")
        self.assertEqual(socket.sent[1]["response"]["conversation"], "none")
        self.assertEqual(json.loads(socket.sent[1]["response"]["input"][0]["content"][0]["text"])["sentence_to_read_verbatim"], "A clear sentence.")
        self.assertTrue(calls[0][0][0].startswith("wss://provider.example/v1/realtime?"))
        self.assertIsNone(calls[0][1]["proxy"])
        failure = RuntimeError("redirect-fixture")
        self.assertIs(PinnedConnect.process_redirect(None, failure), failure)

    async def test_wrong_words_decimal_and_unrequested_output_are_rejected(self):
        self.assertEqual(normalize_speech("Let's begin!"), normalize_speech("Lets begin."))
        self.assertNotEqual(normalize_speech("a part"), normalize_speech("apart"))
        self.assertNotEqual(normalize_speech("1.2"), normalize_speech("12"))
        for events in (self.events("An entirely different sentence."),
                       self.events("A clear sentence.")[:2] + [{"type": "response.function_call_arguments.done"}],
                       self.events("A clear sentence.")[:2] + [{"type": "response.output_text.delta", "delta": "Unexpected answer"}]):
            socket = FakeSocket(events)
            with patch("reign.team_assistant.configuration", return_value=PROVIDER), self.assertRaises(TeamError):
                await Narrator(lambda *args, **kwargs: socket).generate([{"text": "A clear sentence.", "expression": "neutral"}])


if __name__ == "__main__": unittest.main()
