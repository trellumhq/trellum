"""The shipped <trellum-report> element: its inbound origin check, behaviourally.

static/report_embed_element.js is served verbatim at /api/reports/embed-element.js
and runs on a *customer's* page, so a regression here breaks embeds we cannot
see and cannot redeploy. The check that matters most is the one a host would
otherwise have to write itself and usually gets wrong: an inbound message is
acted on only when it comes from this frame (``e.source``) and from the src's
own origin (``e.origin``); outbound messages are addressed to that origin.

The script is fetched through the Django test client (so the route and the
bytes served are what is tested) and executed by Node against a minimal DOM.
"""
import json
import shutil
import subprocess

import pytest
from django.conf import settings

ELEMENT_JS = settings.BASE_DIR / "static" / "report_embed_element.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PORTAL = "https://portal.example.com"

HARNESS = r"""
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const listeners = [], defined = {};
class FakeNode {
  constructor() { this._attrs = {}; this.style = {}; this.events = []; this.children = []; }
  getAttribute(n) { return n in this._attrs ? this._attrs[n] : null; }
  setAttribute(n, v) { this._attrs[n] = String(v); }
  appendChild(c) { this.children.push(c); }
  dispatchEvent(e) { this.events.push({ type: e.type, detail: e.detail || null }); }
}
class CustomEvent { constructor(type, init) { this.type = type; this.detail = (init || {}).detail; } }
const customElements = { define: (n, c) => { defined[n] = c; }, get: (n) => defined[n] };
globalThis.HTMLElement = FakeNode;
globalThis.CustomEvent = CustomEvent;
globalThis.customElements = customElements;
globalThis.location = { href: 'https://host.example/page' };
globalThis.document = { createElement: () => new FakeNode() };
globalThis.window = {
  customElements, CustomEvent,
  addEventListener: (t, f) => { if (t === 'message') listeners.push(f); },
  removeEventListener: () => {},
};
(0, eval)(code);

function mount(attrs) {
  const el = new defined['trellum-report']();
  Object.assign(el._attrs, attrs);
  el.connectedCallback();
  const posted = [];
  el._frame.contentWindow = { postMessage: (m, o) => posted.push({ msg: m, origin: o }) };
  return { el, posted, send: (e) => listeners[listeners.length - 1](e) };
}

const out = {};
const framed = mount({ src: 'https://portal.example.com/share/tok/', theme: 'dark' });
const frameWin = framed.el._frame.contentWindow;
const send = (origin, source, data) => framed.send({ origin, source, data });

send('https://evil.example', frameWin, { type: 'trellum:height', height: 111 });
send('https://portal.example.com.evil.example', frameWin, { type: 'trellum:height', height: 222 });
send('http://portal.example.com', frameWin, { type: 'trellum:height', height: 333 });
send('https://portal.example.com', {}, { type: 'trellum:height', height: 444 });
send('https://evil.example', frameWin, { type: 'trellum:ready' });
out.rejected = { events: framed.el.events.slice(), height: framed.el._frame.style.height || null, posted: framed.posted.slice() };

send('https://portal.example.com', frameWin, { type: 'trellum:height', height: 700 });
send('https://portal.example.com', frameWin, { type: 'trellum:ready' });
out.accepted = { events: framed.el.events.slice(), height: framed.el._frame.style.height, posted: framed.posted.slice() };

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def served_source(tmp_path_factory, django_db_blocker):
    from django.test import Client
    with django_db_blocker.unblock():
        body = Client().get("/api/reports/embed-element.js").content
    path = tmp_path_factory.mktemp("embed-element") / "embed-element.js"
    path.write_bytes(body)
    return path


def _run(served_source, tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(
        ["node", str(harness), str(served_source)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_served_script_is_the_shipped_source(served_source):
    assert served_source.read_bytes() == ELEMENT_JS.read_bytes()


def test_node_check_passes():
    result = subprocess.run(["node", "--check", str(ELEMENT_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_inbound_messages_need_this_frame_and_the_src_origin(served_source, tmp_path):
    out = _run(served_source, tmp_path)
    # Another origin, a look-alike host, the wrong scheme, or another window
    # (even with the right origin) is ignored: no resize, no events, no reply.
    assert out["rejected"] == {"events": [], "height": None, "posted": []}
    accepted = out["accepted"]
    assert accepted["height"] == "700px"
    assert [e["type"] for e in accepted["events"]] == ["trellum:height", "trellum:ready"]
    assert accepted["events"][0]["detail"] == {"height": 700}


def test_outbound_messages_are_addressed_to_the_src_origin(served_source, tmp_path):
    posted = _run(served_source, tmp_path)["accepted"]["posted"]
    assert posted == [{"msg": {"type": "trellum:theme", "theme": "dark"}, "origin": PORTAL}]

