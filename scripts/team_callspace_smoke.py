"""Public UI navigation shared by Workspace's existing browser acceptance.

These helpers click the actual drawer controls. They never call the layout
controller or bypass disabled application buttons.
"""

from playwright.async_api import expect


TRIGGERS = {
    "menu": "#callspaceMenu",
    "settings": "#callspaceSettings",
    "actions": "#callspaceActions",
}


async def close_callspace_panel(page):
    for selector in TRIGGERS.values():
        trigger = page.locator(selector)
        if await trigger.count() and await trigger.get_attribute("aria-expanded") == "true":
            await page.keyboard.press("Escape")
            await expect(trigger).to_have_attribute("aria-expanded", "false")


async def callspace_control(page, selector):
    """Reveal an original Workspace control through its containing drawer."""
    control = page.locator(selector)
    await control.wait_for(state="attached")
    placement = await control.evaluate("""element => ({
        panel: element.closest('[data-callspace-panel]')?.dataset.callspacePanel || null,
        section: element.closest('.callspace-menu-section[data-section]')?.dataset.section || null,
        modal: Boolean(element.closest('dialog')?.open)
    })""")
    panel = placement["panel"]
    if panel:
        assert panel in TRIGGERS, f"Unknown Workspace drawer: {panel}"
        trigger = page.locator(TRIGGERS[panel])
        if await trigger.get_attribute("aria-expanded") != "true":
            await close_callspace_panel(page)
            await trigger.click()
            await expect(trigger).to_have_attribute("aria-expanded", "true")
        if panel == "menu" and placement["section"]:
            section = placement["section"]
            assert section in {"rooms", "documents", "browser"}, section
            navigation = page.locator("#callspaceNav" + section.title())
            if await navigation.get_attribute("aria-current") != "page":
                await navigation.click()
            await expect(navigation).to_have_attribute("aria-current", "page")
    elif not placement["modal"]:
        await close_callspace_panel(page)
    await expect(control).to_be_visible()
    return control


async def callspace_click(page, selector):
    await (await callspace_control(page, selector)).click()


async def settled_callspace_view(page, mode):
    """Wait for finite layout transitions, then check the rendered mode."""
    await expect(page.locator("#callspaceShell")).to_have_attribute("data-mode", mode)
    geometry = await page.evaluate("""async () => {
        const shell = document.getElementById('callspaceShell');
        const face = document.getElementById('callspaceFace');
        const transcript = document.getElementById('callspaceTranscript');
        const targets = [face, transcript, document.getElementById('callspaceActivity')].filter(Boolean);
        for (let pass = 0; pass < 8; pass++) {
            // A frame lets resize observers and style changes create their CSS
            // transitions before getAnimations reads them. Portrait animation
            // runs independently; no clock or media playback is paused here.
            await new Promise(resolve => requestAnimationFrame(resolve));
            const active = targets.flatMap(target => target.getAnimations()).filter(animation =>
                (animation.playState === 'running' || animation.pending) &&
                Number.isFinite(animation.effect?.getComputedTiming().endTime));
            if (!active.length) {
                const rect = node => {
                    const value = node.getBoundingClientRect();
                    return {x: value.x, y: value.y, width: value.width, height: value.height};
                };
                const style = getComputedStyle(transcript);
                return {shell: rect(shell), face: rect(face), transcript: {
                    visibility: style.visibility, opacity: Number(style.opacity),
                    inert: transcript.inert, ariaHidden: transcript.getAttribute('aria-hidden')
                }};
            }
            await Promise.all(active.map(animation => animation.finished.catch(error => {
                if (error.name !== 'AbortError') throw error;
            })));
        }
        throw Error('Workspace layout kept transitioning instead of settling');
    }""")
    transcript = geometry["transcript"]
    if mode == "call":
        for dimension in ("x", "y", "width", "height"):
            assert abs(geometry["face"][dimension] - geometry["shell"][dimension]) <= 1, geometry
        assert transcript["visibility"] == "hidden" and transcript["opacity"] <= .01, geometry
        assert transcript["inert"] is True and transcript["ariaHidden"] == "true", geometry
    else:
        assert transcript["visibility"] == "visible" and transcript["opacity"] >= .99, geometry
        assert transcript["inert"] is False and transcript["ariaHidden"] == "false", geometry
    return geometry


async def callspace_mode(page, mode):
    assert mode in {"chat", "split", "call"}
    previous_mode = await page.locator("#callspaceShell").get_attribute("data-mode")
    previous = await settled_callspace_view(page, previous_mode)
    await callspace_click(page, "#callspaceMode" + mode.title())
    await close_callspace_panel(page)
    geometry = await settled_callspace_view(page, mode)
    if previous_mode == "chat" and mode == "split":
        assert geometry["face"]["width"] > previous["face"]["width"] + 20, (previous, geometry)
        assert geometry["face"]["height"] > previous["face"]["height"] + 20, (previous, geometry)
    return geometry


async def callspace_media_roundtrip(page):
    """Exercise presentation changes while the caller's real media stays live."""
    await close_callspace_panel(page)
    await settled_callspace_view(page, "chat")
    await page.evaluate("""() => {
        const ids = ['reignAvatar', 'reignLiveAudio', 'cameraPreview', 'callParticipants',
                     'eventFeed', 'messageInput', 'chatForm'];
        const feed = document.getElementById('eventFeed');
        feed.scrollTop = Math.min(24, Math.max(0, feed.scrollHeight - feed.clientHeight));
        window.__callspaceContinuity = {
            nodes: ids.map(id => document.getElementById(id)),
            videos: [...document.querySelectorAll('#callParticipants video')],
            streams: [...document.querySelectorAll('audio, video')].map(node => [node, node.srcObject]),
            message: document.getElementById('messageInput').value,
            transcript: feed.textContent, scrollTop: feed.scrollTop,
            canvasWidth: document.getElementById('reignAvatar').width,
            canvasHeight: document.getElementById('reignAvatar').height
        };
    }""")
    await callspace_mode(page, "split")
    face = page.locator("#callspaceFace")
    await face.focus()
    await face.press("End")
    await settled_callspace_view(page, "call")
    await face.press("Home")
    await settled_callspace_view(page, "chat")
    await page.wait_for_function("""() => {
        const saved = window.__callspaceContinuity;
        return Math.abs(document.getElementById('eventFeed').scrollTop - saved.scrollTop) <= 1;
    }""")
    result = await page.evaluate("""() => {
        const saved = window.__callspaceContinuity;
        const checks = {
            sameNodes: saved.nodes.every(node => node.isConnected && document.getElementById(node.id) === node),
            sameVideos: saved.videos.every(node => node.isConnected &&
                [...document.querySelectorAll('#callParticipants video')].includes(node)),
            sameStreams: saved.streams.every(([node, stream]) => node.srcObject === stream),
            activeTracks: saved.streams.every(([, stream]) => !stream ||
                stream.getTracks().every(track => track.readyState === 'live')),
            sameDraft: document.getElementById('messageInput').value === saved.message,
            sameTranscript: document.getElementById('eventFeed').textContent === saved.transcript,
            sameCanvas: document.getElementById('reignAvatar').width === saved.canvasWidth &&
                document.getElementById('reignAvatar').height === saved.canvasHeight,
            transcriptRestored: !document.getElementById('callspaceTranscript').inert,
            participantVideos: saved.videos.length,
            preservedScrollTop: saved.scrollTop
        };
        delete window.__callspaceContinuity;
        return checks;
    }""")
    assert all(value is True for key, value in result.items()
               if key not in {"participantVideos", "preservedScrollTop"}), result
    return result
