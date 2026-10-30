"""Point-and-click element picking.

Someone who does not write CSS cannot fill in a selector field, which made
building a workflow the one step in this product that needed a developer. This
opens the target page, shows a screenshot, and turns a click into a selector.

The page is kept open between requests so clicking stays instant, which is why
this holds a session rather than opening a browser per call.
"""

import asyncio
import base64

from playwright.async_api import async_playwright

import db
from runner import check_host_allowed, launch_browser, robots_allows

VIEWPORT = {"width": 1280, "height": 800}
SCROLL_STEP = 600
IDLE_TIMEOUT = 600  # close an abandoned session after ten minutes

# Finds the element under a point and works out the shortest selector that
# uniquely identifies it. Runs in the page, so it can test candidates directly.
PICK_SCRIPT = """
([x, y]) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return null;

  const esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : v.replace(/["\\\\]/g, '\\\\$&');
  const unique = (sel) => {
    try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
  };

  const tag = el.tagName.toLowerCase();
  const candidates = [];

  for (const attr of ['data-testid', 'data-test', 'data-cy']) {
    const v = el.getAttribute(attr);
    if (v) candidates.push(`[${attr}="${esc(v)}"]`);
  }
  if (el.id) candidates.push('#' + esc(el.id));
  if (el.getAttribute('name')) candidates.push(`${tag}[name="${esc(el.getAttribute('name'))}"]`);
  if (el.getAttribute('aria-label')) candidates.push(`${tag}[aria-label="${esc(el.getAttribute('aria-label'))}"]`);
  if (el.getAttribute('placeholder')) candidates.push(`${tag}[placeholder="${esc(el.getAttribute('placeholder'))}"]`);
  if (tag === 'input' && el.type) candidates.push(`input[type="${esc(el.type)}"]`);

  const classes = (el.className && typeof el.className === 'string')
    ? el.className.trim().split(/\\s+/).filter((c) => c && !/^(is-|has-)?(active|open|hover|focus|selected)$/.test(c))
    : [];
  if (classes.length) {
    candidates.push(tag + '.' + classes.map(esc).join('.'));
    candidates.push('.' + esc(classes[0]));
  }

  let selector = candidates.find(unique) || null;

  if (!selector) {
    // Walk up building a path, stopping as soon as it is unique.
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift('#' + esc(node.id)); break; }
      const siblings = node.parentElement
        ? [...node.parentElement.children].filter((c) => c.tagName === node.tagName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      parts.unshift(part);
      if (unique(parts.join(' > '))) break;
      node = node.parentElement;
    }
    selector = parts.join(' > ');
  }

  const rect = el.getBoundingClientRect();
  const text = (el.innerText || el.value || '').trim().slice(0, 120);

  let suggestion = 'assert_text';
  if (tag === 'input' || tag === 'textarea' || tag === 'select') suggestion = 'fill';
  else if (tag === 'button' || tag === 'a' || el.getAttribute('role') === 'button') suggestion = 'click';
  else if (text) suggestion = 'extract';

  return {
    selector,
    tag,
    type: el.getAttribute('type') || '',
    text,
    placeholder: el.getAttribute('placeholder') || '',
    matches: (() => { try { return document.querySelectorAll(selector).length; } catch (e) { return 0; } })(),
    suggestion,
    box: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
  };
}
"""


class PickerError(Exception):
    """Raised with a message meant to be shown to the operator."""


class Picker:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        self.url = None

    @property
    def is_open(self):
        return self._page is not None

    async def open(self, url):
        check_host_allowed(url)
        if db.get_setting("respect_robots_txt", "1") == "1" and not robots_allows(url):
            raise PickerError(f"robots.txt disallows automated access to {url}")

        async with self._lock:
            await self._close_locked()
            settings = db.get_settings()
            self._pw = await async_playwright().start()
            try:
                self._browser, _ = await launch_browser(
                    self._pw,
                    settings.get("browser_channel", "chrome") or None,
                    True,  # always headless: the operator sees the screenshot
                )
                context = await self._browser.new_context(viewport=VIEWPORT)
                self._page = await context.new_page()
                await self._page.goto(url, timeout=30000, wait_until="domcontentloaded")
                self.url = url
            except Exception:
                await self._close_locked()
                raise
            return await self._shot_locked()

    async def scroll(self, direction):
        async with self._lock:
            self._require_page()
            delta = SCROLL_STEP if direction == "down" else -SCROLL_STEP
            await self._page.evaluate("(d) => window.scrollBy(0, d)", delta)
            return await self._shot_locked()

    async def pick(self, x, y):
        async with self._lock:
            self._require_page()
            result = await self._page.evaluate(PICK_SCRIPT, [x, y])
            if not result or not result.get("selector"):
                raise PickerError("Nothing is there. Try clicking directly on the element.")
            return result

    async def close(self):
        async with self._lock:
            await self._close_locked()

    # -- internals -----------------------------------------------------------

    def _require_page(self):
        if not self._page:
            raise PickerError("No page is open. Load a URL first.")

    async def _shot_locked(self):
        png = await self._page.screenshot(type="jpeg", quality=70)
        scroll = await self._page.evaluate(
            "() => ({ y: window.scrollY, height: document.body.scrollHeight })"
        )
        return {
            "image": "data:image/jpeg;base64," + base64.b64encode(png).decode(),
            "width": VIEWPORT["width"],
            "height": VIEWPORT["height"],
            "scroll_y": scroll["y"],
            "page_height": scroll["height"],
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def _close_locked(self):
        for closer in (
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer:
                try:
                    await closer()
                except Exception:
                    pass
        self._pw = None
        self._browser = None
        self._page = None
        self.url = None


picker = Picker()
