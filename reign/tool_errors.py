"""Closed public errors for browser effects; never forward provider exceptions."""
from fastapi import HTTPException
from fastapi.responses import JSONResponse


ACTION_MESSAGES = {
    "stale_observation": "The page observation expired or changed. Observe the page again.",
    "invalid_target": "That page element is no longer available. Observe the page again.",
    "secret_field": "Credentials require the secure saved-account tool.",
    "invalid_action": "That browser action is not supported.",
    "origin_blocked": "The destination is outside the permitted website policy.",
    "account_form_unavailable": "A single supported same-origin account form could not be identified.",
    "credential_unavailable": "The saved login could not be used. Reopen Accounts and check its status.",
    "human_verification_required": "This step needs you to take control and complete the site's verification or consent.",
    "action_uncertain": "The action outcome is uncertain. Inspect the page before requesting another action.",
}


class BrowserActionError(HTTPException):
    def __init__(self, code):
        self.code = code if code in ACTION_MESSAGES else "action_uncertain"
        super().__init__(409 if self.code in {"stale_observation", "invalid_target"} else
                         503 if self.code == "action_uncertain" else 403,
                         ACTION_MESSAGES[self.code])


async def browser_action_error_response(request, error):
    return JSONResponse({"code": error.code, "detail": error.detail},
                        status_code=error.status_code, headers={"Cache-Control": "no-store"})
