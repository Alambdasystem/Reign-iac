"""Live-provider gate for one synthetic room reply, image and narrated sentence.

No real camera, employee, document or recording enters this probe. It does not
open a browser, write the team archive, or claim device-call acceptance.
"""
import asyncio
from array import array
import io
import json
import logging
import math
import re
import sys
import uuid
import wave

import httpx
from PIL import Image, ImageDraw

from reign.team_assistant import TeamAssistant
from reign.team_media import TeamMedia


async def run_acceptance():
    receipt = {"verification": "reign_team_provider", "status": "failed",
               "input": "synthetic_image_and_text", "physical_device": "not_run",
               "stage": "model"}
    cache = TeamMedia()
    try:
        async with asyncio.timeout(90):
            picture = Image.new("RGB", (128, 128), "white")
            ImageDraw.Draw(picture).rectangle((24, 24, 104, 104), fill="blue")
            output = io.BytesIO(); picture.save(output, format="JPEG")
            room = str(uuid.uuid4())
            actor = {"id": "reign-owner", "display_name": "Release test", "role": "owner"}
            attachment = cache.put(room, actor["id"], output.getvalue(), "image",
                                   {"source": "synthetic_fixture", "participant_id": actor["id"]})
            context = {"workspace": {"id": "rain", "name": "Rain"},
                       "room": {"id": room, "title": "Disposable release check"},
                       "messages": [], "documents": [], "run_id": str(uuid.uuid4())}
            async with httpx.AsyncClient(trust_env=False, timeout=45) as http:
                result = await TeamAssistant(http, cache)(context, actor,
                    "Identify the simple colored shape in this synthetic picture. Reply with exactly one short sentence, naming the color and shape, in a warm tone. Also draft a Markdown document titled Team release probe containing only the same sentence. This is a disposable test; do not perform any external action.",
                    [attachment], True)
            receipt["stage"] = "validation"
            if ("blue" not in result["text"].lower() or "square" not in result["text"].lower()
                    or len(result["segments"]) != 1 or len(result["documents"]) != 1
                    or result["speech_status"] != "ready"):
                raise ValueError("invalid_probe_result")
            draft = result["documents"][0]["content"]
            if (not draft.strip() or not re.search(r"\bblue\b", draft, re.IGNORECASE)
                    or not re.search(r"\bsquare\b", draft, re.IGNORECASE)):
                raise ValueError("invalid_probe_document")
            item = cache.get(result["segments"][0]["audio_id"], room, "audio")
            with wave.open(io.BytesIO(item["data"]), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000):
                    raise ValueError("invalid_probe_audio")
                samples = array("h"); samples.frombytes(wav.readframes(wav.getnframes()))
                if sys.byteorder != "little": samples.byteswap()
                rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
                duration = len(samples) / 24000
            if rms < .002 or not .25 < duration < 30: raise ValueError("silent_probe_audio")
            receipt.update(status="passed", stage="complete", image_interpreted=True,
                           structured_expression=True, document_draft=True,
                           audio={"duration_seconds": round(duration, 3), "rms_normalized": round(rms, 6)},
                           transcript_matched_sentence=True, archive_writes=0)
    except asyncio.CancelledError:
        raise
    except Exception:
        receipt["error_code"] = "team_provider_probe_failed"
    finally:
        cache.close()
    return receipt


def main():
    logging.disable(logging.CRITICAL)
    receipt = asyncio.run(run_acceptance())
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
