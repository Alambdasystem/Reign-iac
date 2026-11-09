"""Trusted implementations behind the private workspace MCP tool server."""
import uuid


class CoreTools:
    async def status(self):
        from reign.core import ready
        return await ready()

    async def session(self, sid):
        from reign.core import session
        return await session(uuid.UUID(sid))

    async def open(self, url):
        from reign.core import OpenRequest, open_session
        return await open_session(OpenRequest(url=url))

    async def navigate(self, sid, url, epoch):
        from reign.core import NavigateRequest, navigate
        return await navigate(uuid.UUID(sid), NavigateRequest(url=url, epoch=epoch))

    async def observe(self, sid, epoch):
        from reign.core import observe
        return await observe(uuid.UUID(sid), epoch)

    async def action(self, name, sid, arguments, epoch):
        from reign.core import BrowserEffectRequest, browser_effect
        data = BrowserEffectRequest(action=name.removeprefix("browser_"), epoch=epoch, **arguments)
        return await browser_effect(uuid.UUID(sid), data)

    async def vault(self, name, arguments=None, sid=None, epoch=None):
        from reign.core import vault_tool
        return await vault_tool(name, arguments or {}, "admin", sid, epoch)
