"""Bounded DOM observations and private element handles for managed browsing.

Only public descriptors cross the Computer boundary. Selectors, DOM objects,
input values and browser state never enter an observation or workflow result.
"""
from dataclasses import dataclass
import contextlib
import time
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from playwright.async_api import Error as PlaywrightError

from reign.config import checked_url
from reign.tool_errors import ACTION_MESSAGES, BrowserActionError


OBSERVATION_TTL = 60.0
MAX_ELEMENTS = 80
MAX_TEXT = 6000
ACTION_TIMEOUT_MS = 4000
SAFE_KEYS = frozenset({"Enter", "Tab", "Shift+Tab", "Backspace", "Escape",
                       "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight",
                       "Control+A", "Space"})
ERROR_CODES = frozenset(ACTION_MESSAGES)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    epoch: int = Field(ge=0)
    observation_id: str = Field(min_length=1, max_length=80)
    action: str = Field(pattern="^(click|fill|press|scroll)$")
    ref: str | None = Field(default=None, pattern="^e[1-9][0-9]?$", max_length=3)
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=20)
    dy: int | None = Field(default=None, ge=-1000, le=1000)

    @model_validator(mode="after")
    def bounded_shape(self):
        required = {"click": {"ref"}, "fill": {"ref", "text"},
                    "press": {"ref", "key"}, "scroll": {"dy"}}[self.action]
        supplied = {key for key in ("ref", "text", "key", "dy") if getattr(self, key) is not None}
        if supplied != required or (self.action == "press" and self.key not in SAFE_KEYS):
            raise ValueError("Invalid action shape")
        return self


def allowed_origin(url):
    try:
        checked_url(url)
        parsed = urlsplit(url)
        return "https://" + parsed.hostname
    except (ValueError, TypeError):
        raise BrowserActionError("origin_blocked") from None


# The returned object is held privately as a JSHandle. Element references are
# actual nodes in this particular document, never model-supplied selectors.
# A mutation observer rejects page changes while the model decides what to do.
SNAPSHOT_SCRIPT = r"""
({maxElements,maxText,secretNodes,redactions}) => {
  const doc = document, originalURL = location.href;
  const knownSecrets = new Set(secretNodes || []);
  const secretWords = /(?:password|passwd|passphrase|secret|token|api[-_ ]?key|otp|one[-_ ]?time|verification[-_ ]?code|security[-_ ]?code|authenticator|credit[-_ ]?card|card[-_ ]?number|cvv|cvc)/i;
  const revealWords = /(?:show|reveal|display|toggle|copy).*(?:password|secret|token|credential)|(?:password|secret|token|credential).*(?:show|reveal|display|toggle|copy)/i;
  const verificationWords = /(?:captcha|recaptcha|hcaptcha|verif(?:y|ication)|one[-_ ]?time|authenticator|\botp\b|terms|agreement|consent|\bi agree\b|accept.{0,15}(?:policy|policies))/i;
  const signupWords = /(?:sign\s*up|register|create\s+(?:an?\s+)?account)/i;
  const fieldSelector = 'input,textarea,select,[contenteditable]';
  const candidateSelector = 'a[href],button,input,textarea,select,summary,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="combobox"],[role="textbox"],[contenteditable="true"]';
  const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
  const redact = value => (redactions || []).reduce((text,secret) => secret ? text.split(secret).join('[redacted]') : text,String(value || ''));
  const visible = el => {
    if (!el || !el.isConnected || !el.getClientRects().length) return false;
    for (let p=el; p && p.nodeType===1; p=p.parentElement) {
      const style = getComputedStyle(p);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.opacity === '0' || p.hidden || p.inert || p.getAttribute('aria-hidden') === 'true') return false;
    }
    return true;
  };
  const inViewport = el => {
    const rect=el.getBoundingClientRect();
    return rect.bottom>0 && rect.right>0 && rect.top<innerHeight && rect.left<innerWidth;
  };
  const label = el => {
    // In particular, do not use .value, innerHTML or outerHTML here.
    const labels = el.labels ? Array.from(el.labels).map(x => x.textContent).join(' ') : '';
    const referenced = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => doc.getElementById(id)?.textContent || '').join(' ');
    const text = el.matches(fieldSelector) ? '' : el.textContent;
    return norm(el.getAttribute('aria-label') || referenced || labels || text || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name'));
  };
  const fieldHints = el => [el.id,el.getAttribute('name'),el.getAttribute('autocomplete'),el.getAttribute('placeholder'),el.getAttribute('aria-label'),label(el)].join(' ');
  const password = el => el.tagName === 'INPUT' && (el.type === 'password' || /(?:current|new)-password/i.test(el.autocomplete || ''));
  const formHasSecret = form => !!form && Array.from(form.elements).some(el => password(el) || knownSecrets.has(el));
  const username = form => {
    if (!form) return null;
    const fields=Array.from(form.elements).filter(el => visible(el) && el.tagName==='INPUT' && ['text','email'].includes(el.type) && !el.disabled && !el.readOnly && !secretWords.test(fieldHints(el)));
    const explicit=fields.filter(el => /(?:^|\s)username(?:\s|$)/i.test(el.autocomplete || '') || /(?:user[-_ ]?name|login[-_ ]?(?:name|id)?|account[-_ ]?name)/i.test(fieldHints(el)));
    return explicit.length===1 ? explicit[0] : (explicit.length===0 && fields.length===1 ? fields[0] : null);
  };
  const signupForm = form => !!form && (Array.from(form.elements).filter(password).length>1 || Array.from(form.elements).some(el => /new-password/i.test(el.autocomplete || '')) || /(?:sign[_/-]?up|register|registration)/i.test(form.action));
  const isSecret = el => {
    if (knownSecrets.has(el) || password(el) || el.closest('[data-secret],[data-sensitive]')) return true;
    if (el.matches(fieldSelector) && secretWords.test(fieldHints(el))) return true;
    // A username/email in a credential form belongs to the private broker too.
    const form=el.form || el.closest('form');
    return formHasSecret(form) && username(form)===el;
  };
  const role = el => el.getAttribute('role') || ({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox',SUMMARY:'button'}[el.tagName]) || (el.tagName==='INPUT' ? ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'}[el.type] || 'textbox') : 'element');
  const descriptor = (el, index) => {
    const secret = isSecret(el), type = el.tagName==='INPUT' ? el.type : '';
    return {ref:'e'+(index+1),tag:el.tagName.toLowerCase(),role:role(el),name:redact(label(el)).slice(0,160),type,
      disabled:!!el.disabled || el.getAttribute('aria-disabled')==='true',secret,
      checked:['checkbox','radio'].includes(type) ? (el.indeterminate ? null : !!el.checked) : (['checkbox','radio'].includes(role(el)) && ['true','false'].includes(el.getAttribute('aria-checked')) ? el.getAttribute('aria-checked')==='true' : null),
      fillable:!secret && !el.readOnly && !el.disabled && ((el.tagName==='INPUT' && ['text','email','url','search','tel','number'].includes(type)) || el.tagName==='TEXTAREA')};
  };
  const targetInfo = el => {
    const form = el.form || el.closest('form'), link = el.closest('a[href]');
    const submits = !!form && ((el.tagName==='BUTTON' && el.type==='submit') || (el.tagName==='INPUT' && ['submit','image'].includes(el.type)));
    return {secret:isSecret(el),reveal:revealWords.test(fieldHints(el)),humanVerification:verificationWords.test(fieldHints(el)),signupForm:signupForm(form),
      signupControl:signupWords.test(fieldHints(el)) && (!link || role(el)==='button'),
      formCredential:formHasSecret(form),formAction:form ? (el.getAttribute('formaction') ? el.formAction : form.action) : null,
      formMethod:form ? (el.getAttribute('formmethod') || form.method || 'get').toLowerCase() : null,
      href:link ? link.href : null,submits,tag:el.tagName.toLowerCase(),type:el.type || '',
      fillable:descriptor(el,0).fillable,disabled:!!el.disabled || el.getAttribute('aria-disabled')==='true'};
  };
  const signature = el => JSON.stringify([el.tagName,el.type,el.id,el.getAttribute('name'),el.getAttribute('role'),
    el.getAttribute('autocomplete'),el.getAttribute('contenteditable'),el.readOnly,el.disabled,el.checked,el.indeterminate,el.getAttribute('aria-checked'),label(el),targetInfo(el)]);
  const candidates = Array.from(doc.querySelectorAll(candidateSelector)).filter(el => visible(el) && inViewport(el) && !el.closest('x-pw-glass') &&
    !(el.tagName==='INPUT' && ['hidden','file'].includes(el.type)));
  const nodes = candidates.slice(0,maxElements), signatures = nodes.map(signature);
  const elements = nodes.map(descriptor);
  const pieces = []; let size=0, scanned=0, textTruncated=false;
  const walker = doc.createTreeWalker(doc.body || doc.documentElement,NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    if (++scanned > 12000) { textTruncated=true; break; }
    const node=walker.currentNode, parent=node.parentElement;
    if (!parent || parent.closest('script,style,noscript,template,input,textarea,select,option,[contenteditable],iframe,canvas,svg,x-pw-glass,[data-secret],[data-sensitive]') || !visible(parent)) continue;
    const value=redact(norm(node.textContent));
    if (!value) continue;
    if (size+value.length+1 > maxText) { pieces.push(value.slice(0,Math.max(0,maxText-size))); textTruncated=true; break; }
    pieces.push(value); size+=value.length+1;
  }
  const accountForms = Array.from(doc.forms).map(form => {
    const fields=Array.from(form.elements).filter(visible);
    const passwords=fields.filter(password);
    const user=username(form);
    const pair=passwords.length===2 && !passwords.some(el => /current-password/i.test(el.autocomplete || '')) &&
      (passwords.every(el => /new-password/i.test(el.autocomplete || '')) || passwords.some(el => /confirm|repeat|retype|reenter|re-enter/i.test(fieldHints(el))));
    if (!user || !(passwords.length===1 || pair) || passwords.some(el => el.disabled || el.readOnly)) return null;
    return {form,username:user,passwords,action:form.action,method:(form.method || 'get').toLowerCase(),
      userSignature:signature(user),passwordSignatures:passwords.map(signature)};
  }).filter(Boolean);
  const account = accountForms.length===1 ? accountForms[0] : null;
  let revision=0;
  // Playwright installs its non-interactive screenshot mask in a private shadow
  // tree under x-pw-glass. Frame polling must not invalidate the page itself.
  const changed = records => records.some(record => {
    if (record.target.nodeType===1 && record.target.tagName==='X-PW-GLASS') return false;
    return !(record.type==='childList' && [...record.addedNodes,...record.removedNodes].length &&
      [...record.addedNodes,...record.removedNodes].every(node => node.nodeType===1 && node.tagName==='X-PW-GLASS'));
  });
  const observer = new MutationObserver(records => { if (changed(records)) revision++; });
  observer.observe(doc.documentElement,{subtree:true,childList:true,characterData:true,attributes:true});
  const current = () => doc===document && originalURL===location.href;
  const fresh = () => {
    if (changed(observer.takeRecords())) revision++;
    return current() && revision===0;
  };
  let removeGuard=()=>{}, guardBlocked=false;
  const installGuard = (el, check, events) => {
    removeGuard(); guardBlocked=false;
    const listener = event => {
      if (!event.isTrusted) return;
      if (guardBlocked || !(event.target===el || el.contains(event.target)) || check().error) {
        guardBlocked=true; event.preventDefault(); event.stopImmediatePropagation(); return;
      }
      // Recheck at the first actual input event, after Playwright's auto-wait.
      // Subsequent DOM changes caused by that intended input are its result.
      removeGuard();
    };
    for (const event of events) doc.addEventListener(event,listener,{capture:true,passive:false});
    removeGuard=()=>{for (const event of events) doc.removeEventListener(event,listener,true);};
  };
  const snapshot = {
    payload:{text:pieces.join('\n').slice(0,maxText),elements,truncated:textTruncated || candidates.length>maxElements},
    document:doc,nodes,account,
    check(index) {
      if (!fresh()) return {error:'stale_observation'};
      if (index===null) return {ok:true};
      const el=nodes[index];
      if (!el || !el.isConnected || el.ownerDocument!==doc || !visible(el) || signature(el)!==signatures[index]) return {error:'invalid_target'};
      return {ok:true,...targetInfo(el)};
    },
    checkAccount(requireFresh=true) {
      if (!(requireFresh ? fresh() : current())) return {error:'stale_observation'};
      if (!account) return {error:'account_form_unavailable'};
      const {form,username,passwords}=account;
      if (!form.isConnected || !username.isConnected || username.ownerDocument!==doc || username.form!==form || !visible(username) || signature(username)!==account.userSignature || passwords.some((password,i) => !password.isConnected || password.ownerDocument!==doc || password.form!==form || !visible(password) || signature(password)!==account.passwordSignatures[i])) return {error:'account_form_unavailable'};
      return {ok:true,action:form.action,method:(form.method || 'get').toLowerCase()};
    },
    arm(index, action) {
      const result=this.check(index);
      if (result.error) return result;
      installGuard(nodes[index],()=>this.check(index),action==='click' ? ['pointerdown','mousedown','click'] : action==='press' ? ['keydown'] : ['beforeinput']);
      return {ok:true};
    },
    armAccount(index) {
      const result=this.checkAccount(false);
      if (result.error) return result;
      installGuard(index===-1 ? account.username : account.passwords[index],()=>this.checkAccount(false),['beforeinput']);
      return {ok:true};
    },
    guardBlocked() { return guardBlocked; },
    dispose() { removeGuard(); observer.disconnect(); }
  };
  return snapshot;
}
"""


@dataclass
class Observation:
    id: str
    epoch: int
    url: str
    origin: str
    created: float
    handle: object

    @classmethod
    async def capture(cls, page, epoch, secret_nodes=(), redactions=()):
        origin = allowed_origin(page.url)
        handle = await page.evaluate_handle(SNAPSHOT_SCRIPT, {
            "maxElements": MAX_ELEMENTS, "maxText": MAX_TEXT, "secretNodes": list(secret_nodes),
            "redactions": list(redactions),
        })
        return cls(str(uuid4()), epoch, page.url, origin, time.monotonic(), handle)

    def check_context(self, page, epoch):
        if epoch != self.epoch or page.url != self.url or time.monotonic() - self.created > OBSERVATION_TTL:
            raise BrowserActionError("stale_observation")
        if allowed_origin(page.url) != self.origin:
            raise BrowserActionError("origin_blocked")

    async def payload(self):
        return await self.handle.evaluate("s => s.payload")

    async def target(self, ref=None):
        index = int(ref[1:]) - 1 if ref else None
        result = await self.handle.evaluate("(s,index) => s.check(index)", index)
        if result.get("error"):
            raise BrowserActionError(result["error"])
        return result

    async def element(self, ref):
        return (await self.handle.evaluate_handle("(s,index) => s.nodes[index]", int(ref[1:]) - 1)).as_element()

    async def arm(self, ref, action):
        result = await self.handle.evaluate("(s,args) => s.arm(args.index,args.action)",
                                            {"index": int(ref[1:]) - 1, "action": action})
        if result.get("error"):
            raise BrowserActionError(result["error"])

    async def arm_account(self, index):
        result = await self.handle.evaluate("(s,index) => s.armAccount(index)", index)
        if result.get("error"):
            raise BrowserActionError(result["error"])

    async def check_dispatch(self, *, navigation_allowed=False):
        try:
            if await self.handle.evaluate("s => s.guardBlocked()"):
                raise BrowserActionError("action_uncertain")
        except PlaywrightError as error:
            # A completed native click/Enter can legitimately replace the whole
            # document. Only that known context-loss case is accepted; the worker
            # still verifies and returns the actual allowed destination below.
            if navigation_allowed and any(message in error.message for message in (
                "Execution context was destroyed", "Cannot find context with specified id")):
                return
            raise

    async def account(self, *, fresh=True):
        result = await self.handle.evaluate("(s,fresh) => s.checkAccount(fresh)", fresh)
        if result.get("error"):
            raise BrowserActionError(result["error"])
        if result["method"] != "post" or allowed_origin(result["action"]) != self.origin:
            raise BrowserActionError("origin_blocked")
        return result

    async def account_fields(self):
        username = (await self.handle.evaluate_handle("s => s.account.username")).as_element()
        count = await self.handle.evaluate("s => s.account.passwords.length")
        passwords = [(await self.handle.evaluate_handle("(s,i) => s.account.passwords[i]", i)).as_element()
                     for i in range(count)]
        return username, passwords

    async def dispose(self):
        with contextlib.suppress(Exception):
            await self.handle.evaluate("s => s.dispose()")
        with contextlib.suppress(Exception):
            await self.handle.dispose()


async def validate_target(observation, request, credential_origin=None):
    target = await observation.target(request.ref)
    if request.action == "scroll":
        return
    if target.get("humanVerification") or target.get("signupControl") or (target.get("signupForm") and (target.get("submits") or (request.action == "press" and request.key == "Enter"))):
        raise BrowserActionError("human_verification_required")
    if target.get("secret") or target.get("reveal"):
        raise BrowserActionError("secret_field")
    if target.get("disabled"):
        raise BrowserActionError("invalid_target")
    # A credential form can only be submitted, never poked by a generic toggle.
    if target.get("formCredential") and target.get("tag") in {"button", "a"} and not target.get("submits"):
        raise BrowserActionError("secret_field")
    if request.action == "fill" and not target.get("fillable"):
        raise BrowserActionError("invalid_target")
    if target.get("href"):
        destination = allowed_origin(target["href"])
        if credential_origin and destination != credential_origin:
            raise BrowserActionError("origin_blocked")
    if target.get("formAction"):
        destination = allowed_origin(target["formAction"])
        if target.get("formCredential") and (destination != observation.origin or target.get("formMethod") != "post"):
            raise BrowserActionError("origin_blocked")

