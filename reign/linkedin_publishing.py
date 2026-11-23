"""Opt-in LinkedIn text posting through the official Posts API.

Tokens and author identity are server-side configuration, never model input.
The owner console confirms an exact, single-use preview before requesting
publication. A provider submission is never automatically retried.
"""
import os
import re
import httpx

POSTS_URL = "https://api.linkedin.com/rest/posts"
AUTHOR_RE = re.compile(r"^urn:li:(?:person|organization):[A-Za-z0-9_-]{2,100}$")


def configuration():
    author = os.getenv("REIGN_LINKEDIN_AUTHOR_URN", "").strip()
    token = os.getenv("REIGN_LINKEDIN_ACCESS_TOKEN", "")
    enabled = os.getenv("REIGN_LINKEDIN_PUBLISH_ENABLED", "false").lower() == "true"
    version = os.getenv("REIGN_LINKEDIN_VERSION", "202611")
    valid = bool(AUTHOR_RE.fullmatch(author) and token and re.fullmatch(r"20[0-9]{4}", version))
    return {"author": author if AUTHOR_RE.fullmatch(author) else "",
            "ready": valid and enabled, "enabled": enabled,
            "version": version if re.fullmatch(r"20[0-9]{4}", version) else ""}


def preview(text, *, draft_only=False):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 3000:
        return {"error": "Post must have 1–3000 characters", "code": "invalid_post", "status": 422}
    if draft_only:
        return {"status": "draft", "text": text.strip(), "media_supported": False}
    config = configuration()
    return {"status": "preview", "text": text.strip(), "author": config["author"],
            "ready": config["ready"], "media_supported": False,
            "note": "No post has been made. Owner approval is required before publishing."}


async def publish(text, *, expected_author):
    candidate = preview(text)
    if "error" in candidate:
        return candidate
    config = configuration()
    if not config["ready"]:
        return {"error": "LinkedIn publishing is not configured or enabled",
                "code": "linkedin_unavailable", "status": 503}
    if not isinstance(expected_author, str) or expected_author != config["author"]:
        return {"error": "The configured LinkedIn author changed. Preview the post again.",
                "code": "author_changed", "status": 409}
    payload = {"author": config["author"], "commentary": candidate["text"],
               "visibility": "PUBLIC",
               "distribution": {"feedDistribution": "MAIN_FEED",
                                "targetEntities": [], "thirdPartyDistributionChannels": []},
               "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    headers = {"Authorization": "Bearer " + os.environ["REIGN_LINKEDIN_ACCESS_TOKEN"],
               "Linkedin-Version": config["version"],
               "X-Restli-Protocol-Version": "2.0.0",
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
            response = await client.post(POSTS_URL, headers=headers, json=payload)
    except httpx.RequestError:
        return {"error": "Publishing outcome is uncertain; check LinkedIn before retrying",
                "code": "publish_uncertain", "status": 502}
    if response.status_code >= 500:
        return {"error": "Publishing outcome is uncertain; check LinkedIn before retrying",
                "code": "publish_uncertain", "status": 502}
    if response.status_code != 201:
        return {"error": "LinkedIn did not confirm publication; check permissions and account configuration",
                "code": "linkedin_rejected", "status": response.status_code}
    post_id = response.headers.get("x-restli-id", "")
    if not re.fullmatch(r"urn:li:(?:share|ugcPost):[0-9]+", post_id):
        return {"error": "LinkedIn accepted the request but returned no verifiable post ID; inspect account before retrying",
                "code": "publish_uncertain", "status": 502}
    return {"status": "published", "post_id": post_id, "author": config["author"]}
