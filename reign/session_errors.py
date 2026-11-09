"""Closed public errors for the single-admin managed browser pilot."""
from fastapi import HTTPException
from fastapi.responses import JSONResponse


BROWSER_OCCUPIED_CODE = "browser_occupied"
BROWSER_OCCUPIED_MESSAGE = (
    "A browser session is already open. Reconnect to it or close it before opening another."
)


class BrowserOccupied(HTTPException):
    def __init__(self):
        super().__init__(409, BROWSER_OCCUPIED_MESSAGE)


async def browser_occupied_response(request, error):
    # Never expose a downstream exception or response body as the public error.
    return JSONResponse(
        status_code=409,
        content={"code": BROWSER_OCCUPIED_CODE, "detail": BROWSER_OCCUPIED_MESSAGE},
        headers={"Cache-Control": "no-store"},
    )
