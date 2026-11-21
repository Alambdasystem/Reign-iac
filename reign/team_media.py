"""Ephemeral, room-bound media. Raw camera frames are never put in the archive."""
import asyncio
import base64
from collections import deque
from datetime import datetime, timezone
import io
import time
import uuid
import warnings

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageOps

from reign.team_api import body, get_runtime, require_actor
from reign.team_store import TeamError, identifier
from reign.voice import configuration

MAX_UPLOAD = 512 * 1024
IMAGE_TTL = 300
AUDIO_TTL = 600
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MAX_AUDIO_BYTES = 24_000 * 2 * 30 + 128


class TeamMediaError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "media_unavailable"


ERRORS = {
    "invalid_media": (422, "Share a valid, recent camera or screen image under 512 KB."),
    "media_expired": (410, "That shared image or audio clip expired. Share it again."),
    "media_limit": (429, "Media is busy. Wait a moment and try again."),
    "media_unavailable": (503, "Media could not be processed. Your saved conversation is still available."),
    "transcription_failed": (503, "The recording could not be transcribed. Try again or type your message."),
}


async def media_error_response(request, error):
    status, detail = ERRORS[error.code]
    return JSONResponse({"code": error.code, "detail": detail}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def decode_base64(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4 * ((MAX_UPLOAD + 2) // 3):
        raise TeamMediaError("invalid_media")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise TeamMediaError("invalid_media") from None
    if not 1 <= len(raw) <= MAX_UPLOAD:
        raise TeamMediaError("invalid_media")
    return raw


def clean_image(value):
    if not isinstance(value, str) or "," not in value:
        raise TeamMediaError("invalid_media")
    prefix, encoded = value.split(",", 1)
    if prefix not in {"data:image/png;base64", "data:image/jpeg;base64"}:
        raise TeamMediaError("invalid_media")
    raw = decode_base64(encoded)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if (original.format not in {"JPEG", "PNG"} or original.width < 8 or original.height < 8
                        or original.width * original.height > 2_000_000
                        or getattr(original, "n_frames", 1) != 1):
                    raise ValueError()
                original.load()
                clean = ImageOps.exif_transpose(original).convert("RGB")
                clean.thumbnail((960, 540))
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=78, optimize=False)
                result = output.getvalue()
                if len(result) > MAX_UPLOAD: raise ValueError()
                return result
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise TeamMediaError("invalid_media") from None


class TeamMedia:
    def __init__(self, clock=None):
        self.clock = clock or time.time
        self.items = {}
        self.transcriptions = deque()
        self.pending_transcriptions = 0
        self.pending_images = 0

    def prune(self):
        now = self.clock()
        self.items = {key: value for key, value in self.items.items() if value["expires"] > now}
        while self.transcriptions and self.transcriptions[0][0] <= now - 86400:
            self.transcriptions.popleft()

    def put(self, room_id, user_id, data, kind, metadata=None):
        self.prune()
        limit = MAX_UPLOAD if kind == "image" else MAX_AUDIO_BYTES
        if len(data) > limit or len(self.items) >= 64 or sum(len(item["data"]) for item in self.items.values()) + len(data) > MAX_MEDIA_BYTES:
            raise TeamMediaError("media_limit")
        if kind == "image" and sum(item["kind"] == kind and item["user_id"] == user_id for item in self.items.values()) >= 8:
            raise TeamMediaError("media_limit")
        key = str(uuid.uuid4())
        self.items[key] = {"room_id": room_id, "user_id": user_id, "data": data, "kind": kind,
                           "expires": self.clock() + (IMAGE_TTL if kind == "image" else AUDIO_TTL),
                           "metadata": metadata or {}}
        return key

    def get(self, key, room_id, kind, user_id=None):
        identifier(key); self.prune()
        item = self.items.get(key)
        if not item or item["room_id"] != room_id or item["kind"] != kind or user_id is not None and item["user_id"] != user_id:
            raise TeamMediaError("media_expired")
        return item

    async def validate_attachments(self, actor, room_id, ids):
        if len(ids) > 2: raise TeamMediaError("invalid_media")
        for key in ids:
            self.get(key, room_id, "image", actor["id"])

    def admit_transcription(self, actor):
        self.prune(); now = self.clock()
        recent = [row for row in self.transcriptions if row[0] > now - 60]
        if (self.pending_transcriptions >= 2 or len(self.transcriptions) >= 200 or len(recent) >= 12
                or sum(uid == actor["id"] for _, uid in recent) >= 4):
            raise TeamMediaError("media_limit")
        self.transcriptions.append((now, actor["id"]))
        self.pending_transcriptions += 1

    def close(self):
        self.items.clear()


def media(request):
    value = getattr(request.app.state, "team_media", None)
    if value is None: raise TeamError("team_unavailable")
    return value


def install(app):
    app.add_exception_handler(TeamMediaError, media_error_response)

    @app.post("/api/team/rooms/{room_id}/attachments")
    async def attachment(room_id: str, request: Request):
        actor = await require_actor(request)
        runtime = get_runtime(request)
        await runtime.authorize_room(actor, room_id)
        data = await body(request, {"data_url", "source", "connection_id", "captured_at"},
                          {"data_url", "source", "connection_id", "captured_at"}, maximum=750000)
        source = data["source"]
        if not isinstance(source, str) or source not in {"camera", "screen"}: raise TeamMediaError("invalid_media")
        connection = runtime._connection(actor, room_id, data["connection_id"])
        if not connection[source]: raise TeamMediaError("invalid_media")
        try:
            captured = datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
            if captured.tzinfo is None or not -10 <= time.time() - captured.timestamp() <= 60: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise TeamMediaError("invalid_media") from None
        # Decode outside the event loop. Pixel count is checked before loading.
        cache = media(request)
        if cache.pending_images >= 2: raise TeamMediaError("media_limit")
        cache.pending_images += 1; reserved = True
        try:
            work = asyncio.create_task(asyncio.to_thread(clean_image, data["data_url"]))
            try: clean = await asyncio.shield(work)
            except asyncio.CancelledError:
                def release(done):
                    cache.pending_images -= 1
                    if not done.cancelled(): done.exception()
                work.add_done_callback(release); reserved = False
                raise
        finally:
            if reserved: cache.pending_images -= 1
        await runtime.authorize_room(actor, room_id)
        connection = runtime._connection(actor, room_id, data["connection_id"])
        if not connection[source]: raise TeamMediaError("invalid_media")
        metadata = {"source": source, "participant_id": actor["id"], "captured_at": captured.isoformat()}
        key = cache.put(room_id, actor["id"], clean, "image", metadata)
        return JSONResponse({"attachment": {"id": key, **metadata,
            "expires_at": datetime.fromtimestamp(cache.items[key]["expires"], timezone.utc).isoformat()}},
            headers={"Cache-Control": "no-store"})

    @app.get("/api/team/rooms/{room_id}/audio/{audio_id}")
    async def audio(room_id: str, audio_id: str, request: Request):
        actor = await require_actor(request)
        await get_runtime(request).authorize_room(actor, room_id)
        data = media(request).get(audio_id, room_id, "audio")["data"]
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/api/team/rooms/{room_id}/transcriptions")
    async def transcribe(room_id: str, request: Request):
        actor = await require_actor(request)
        runtime = get_runtime(request)
        await runtime.authorize_room(actor, room_id)
        data = await body(request, {"audio_base64", "mime_type"}, {"audio_base64", "mime_type"}, maximum=750000)
        mime = data["mime_type"].split(";", 1)[0] if isinstance(data["mime_type"], str) else ""
        extension = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav"}.get(mime)
        if not extension: raise TeamMediaError("invalid_media")
        raw = decode_base64(data["audio_base64"])
        # Container signatures prevent arbitrary file uploads; provider decoding
        # remains authoritative for codec/duration and transcription validity.
        valid = (extension == "webm" and raw.startswith(b"\x1aE\xdf\xa3")
                 or extension == "mp4" and raw[4:8] == b"ftyp"
                 or extension == "ogg" and raw.startswith(b"OggS")
                 or extension == "wav" and raw.startswith(b"RIFF") and raw[8:12] == b"WAVE")
        if not valid: raise TeamMediaError("invalid_media")
        cache = media(request); cache.admit_transcription(actor)
        try:
            settings = configuration()
            response = await request.app.state.http.post(settings["base"] + "/audio/transcriptions",
                headers={"Authorization": "Bearer " + settings["key"]},
                files={"file": ("dictation." + extension, raw, mime)},
                data={"model": "gpt-transcribe", "response_format": "json"}, timeout=45, follow_redirects=False)
            if response.status_code != 200 or len(response.content) > 20000:
                raise TeamMediaError("transcription_failed")
            result = response.json().get("text")
            if not isinstance(result, str) or not 1 <= len(result.strip()) <= 4000:
                raise TeamMediaError("transcription_failed")
            await runtime.authorize_room(actor, room_id)
            return JSONResponse({"text": result.strip()}, headers={"Cache-Control": "no-store"})
        except (TeamError, TeamMediaError):
            raise
        except Exception:
            raise TeamMediaError("transcription_failed") from None
        finally:
            cache.pending_transcriptions -= 1
