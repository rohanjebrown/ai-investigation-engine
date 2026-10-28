// Test-only built-in Node runner: no packages, bundler or frontend build step.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const directory = path.resolve(__dirname, "../../src/investigation_engine/infrastructure/api/static");
const script = fs.readFileSync(path.join(directory, "app.js"), "utf8");
const html = fs.readFileSync(path.join(directory, "index.html"), "utf8");
const adminScript = fs.readFileSync(path.join(directory, "admin.js"), "utf8");

// Small event/DOM test double, not a browser/layout emulator. Browser QA is
// separate. Forbid HTML insertion so injection tests exercise safe DOM writes.
class Element {
  constructor(tag, document) {
    this.tagName = tag;
    this.document = document;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.open = false;
    this._text = "";
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set innerHTML(value) { throw new Error("Unsafe HTML insertion"); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  async emit(name, event = {}) { for (const callback of this.listeners[name] || []) await callback(event); }
  focus() { this.document.activeElement = this; }
  scrollIntoView(options) { this.scrolled = options; }
  all() { return [this, ...this.children.flatMap((child) => child.all())]; }
}

function setup(fetcher = async () => reply(answer())) {
  const document = { nodes: new Map(), activeElement: null };
  document.createElement = (tag) => new Element(tag, document);
  document.getElementById = (id) => {
    assert.ok(document.nodes.has(id), `Missing HTML element ${id}`);
    return document.nodes.get(id);
  };
  for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
    const element = document.createElement(match[1]);
    element.hidden = /\bhidden\b/.test(match[2]);
    document.nodes.set(match[3], element);
  }
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  vm.runInNewContext(script, {
    document, AbortController,
    fetch: async (...args) => { requests.push(args); return fetcher(...args); },
    setTimeout(callback, duration) { assert.ok([100000, 300000].includes(duration)); timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  });
  const get = document.getElementById;
  return {
    get, document, requests, timers,
    submit(value) {
      get("question").value = value;
      return get("question-form").emit("submit", { preventDefault() {} });
    },
  };
}

function citation(id = "E1", changes = {}) {
  return { evidence_id: id, evidence: {
    document_id: `document-${id}`, document_version_id: `version-${id}`, content_sha256: "a".repeat(64),
    start: 2, end: 9, text: " A😀 B.\n", title: "Source title", rank: 1, score: 0.8,
    source: { kind: "dataset", source_id: "2Wiki", revision: "pinned", display_name: "Passage",
      record_locator: { split: "dev", context_position: 0 }, metadata: { title: "Original title" } },
    source_spans: [{ start: 0, end: 10, locators: [{ kind: "sentence", index: 0 }] }],
    ...changes,
  } };
}

function answer(citations = [citation()]) {
  return { status: "answered", answer: "A supported answer. [E1]", citations, message: null };
}

function reply(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

test("submits exact question only to the same-origin endpoint and renders multiple mapped citations", async () => {
  const ui = setup(async () => reply(answer([citation("E2"), citation("E1")])));
  await ui.submit("  Which source? 😀\n");
  assert.equal(ui.requests.length, 1);
  const [url, options] = ui.requests[0];
  assert.equal(url, "/questions");
  assert.equal(options.method, "POST");
  assert.deepEqual(JSON.parse(options.body), { question: "  Which source? 😀\n" });
  assert.equal(options.headers["Content-Type"], "application/json");
  assert.equal(ui.get("result").hidden, false);
  assert.equal(ui.get("answer-text").textContent, "A supported answer. [E1]");
  assert.equal(ui.get("submitted-question").textContent, "  Which source? 😀\n");
  assert.equal(ui.get("source-list").children.length, 2);
  assert.match(ui.get("source-list").children[0].textContent, /version-E2/);
  assert.equal(ui.get("citation-links").children[0].textContent, "[E2] Source title");
  assert.equal(ui.get("source-count").textContent, "2 sources");
});

test("investigation mode calls the dedicated endpoint and renders bounded search steps", async () => {
  const hit = citation("E1").evidence;
  const data = {
    status: "answered", answer: "The evidence establishes it. [E1]", citations: ["E1"],
    evidence: [{ evidence_id: "E1", evidence: hit }],
    searches: [{ sequence: 1, tool_name: "search_evidence", query: "derived query", evidence_ids: ["E1"],
      result_count: 1, duration_ms: 4, succeeded: true, disposition: "succeeded" }],
    conflicts: [], generation_requests: 2, embedding_requests: 1, message: null,
    model_version: "test-model", usage: { prompt_tokens: 100, candidate_tokens: 10, thinking_tokens: 0 },
  };
  const ui = setup(async () => reply(data));
  ui.get("question-mode").value = "investigation";
  await ui.submit("Investigate this");
  assert.equal(ui.requests[0][0], "/investigations");
  assert.equal(ui.get("investigation-steps").hidden, false);
  assert.match(ui.get("search-list").textContent, /Step 1: derived query — 1 results/);
  assert.equal(ui.get("source-list").children.length, 1);
});

test("admin interface keeps bearer credentials in page memory and uses safe same-origin rendering", () => {
  assert.match(html, /id="admin-token" type="password"/);
  assert.match(html, /id="document-file" type="file"/);
  assert.match(adminScript, /let bearer = null/);
  assert.match(adminScript, /headers\.set\("Authorization", `Bearer \$\{bearer\}`\)/);
  assert.match(adminScript, /globalThis\.investigationAdminAuthorization/);
  assert.match(adminScript, /fetch\(path/);
  assert.match(adminScript, /textContent = String\(text\)/);
  assert.match(adminScript, /crypto\.randomUUID\(\)/);
  assert.doesNotMatch(adminScript, /localStorage|sessionStorage|document\.cookie|innerHTML/);
});

test("citation selection expands and focuses its corresponding original source span", async () => {
  const ui = setup();
  await ui.submit("q");
  const card = ui.get("source-list").children[0];
  await ui.get("citation-links").children[0].emit("click");
  assert.equal(card.open, true);
  assert.equal(ui.document.activeElement, card.children[0]);
  assert.equal(card.scrolled.block, "nearest");
  assert.match(card.textContent, /sentence index 0 — original text range \[0, 10\)/);
  assert.match(card.textContent, /\[2, 9\) characters/);
  assert.match(card.textContent, / A😀 B\.\n/);
});

test("missing optional metadata produces useful fallback labels without invented locations or links", async () => {
  const hit = citation("E1", { title: null, source_spans: [], source: {
    kind: "upload", source_id: "original-source", display_name: null, revision: null, record_locator: {}, metadata: {},
  } });
  const ui = setup(async () => reply(answer([hit])));
  await ui.submit("q");
  assert.equal(ui.get("citation-links").children[0].textContent, "[E1] Source document");
  assert.match(ui.get("source-list").textContent, /No original page, sentence or line locations/);
  assert.ok(!ui.get("source-list").all().some((node) => ["a", "img", "script"].includes(node.tagName)));
});

test("answer, title, excerpt and metadata payloads are literal text and answer markers never create citations", async () => {
  const payload = '<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>';
  const data = answer([citation("E1", { title: payload, text: payload, source: {
    kind: "dataset", source_id: payload, display_name: null, record_locator: { record: payload },
    metadata: { url: "javascript:alert(1)", title: payload }, revision: null,
  } })]);
  data.answer = `${payload} [E999]`;
  const ui = setup(async () => reply(data));
  await ui.submit("q");
  assert.equal(ui.get("answer-text").textContent, data.answer);
  assert.ok(ui.get("source-list").textContent.includes(payload));
  assert.equal(ui.get("citation-links").children.length, 1);
  assert.ok(!ui.get("source-list").all().some((node) => ["a", "img", "script"].includes(node.tagName)));
});

test("insufficient evidence is a separate non-error outcome and replaces old sources", async () => {
  let second = false;
  const ui = setup(async () => reply(second ? { status: "insufficient_evidence", answer: null, citations: [], message: "No support" } : answer()));
  await ui.submit("first");
  second = true;
  await ui.submit("second");
  assert.equal(ui.get("result").hidden, false);
  assert.equal(ui.get("answer-status").textContent, "Insufficient evidence");
  assert.equal(ui.get("request-error").hidden, true);
  assert.equal(ui.get("sources").hidden, true);
  assert.equal(ui.get("source-list").children.length, 0);
  assert.match(ui.get("answer-text").textContent, /do not establish an answer/);
});

for (const value of ["", " \t\n", "x".repeat(4001)]) {
  test(`rejects invalid input before fetch (${value.length} characters)`, async () => {
    const ui = setup();
    await ui.submit(value);
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.get("question-error").hidden, false);
    assert.equal(ui.get("question").attributes["aria-invalid"], "true");
    assert.equal(ui.document.activeElement, ui.get("question"));
  });
}

test("Unicode input counts Python-compatible code points rather than UTF-16 units", async () => {
  const ui = setup();
  ui.get("question").value = "😀".repeat(4000);
  await ui.get("question").emit("input");
  assert.equal(ui.get("character-count").textContent, "4,000 / 4,000");
  await ui.submit(ui.get("question").value);
  assert.equal(ui.requests.length, 1);
});

test("loading prevents duplicate submits and always restores the form", async () => {
  let resolve;
  const ui = setup(() => new Promise((done) => { resolve = done; }));
  const pending = ui.submit("first");
  assert.equal(ui.get("submit-question").disabled, true);
  assert.equal(ui.get("loading-indicator").hidden, false);
  assert.equal(ui.get("question-form").attributes["aria-busy"], "true");
  await ui.submit("accidental duplicate");
  assert.equal(ui.requests.length, 1);
  resolve(reply(answer()));
  await pending;
  assert.equal(ui.get("submit-question").disabled, false);
  assert.equal(ui.get("loading-indicator").hidden, true);
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.get("submitted-question").textContent, "first");
});

test("editing or invalid input preserves prior results until a new valid request begins", async () => {
  let next;
  let deferred = false;
  const ui = setup(() => deferred ? new Promise((done) => { next = done; }) : reply(answer()));
  await ui.submit("first");
  ui.get("question").value = "edited";
  await ui.get("question").emit("input");
  assert.equal(ui.get("result").hidden, false);
  await ui.submit(" ");
  assert.equal(ui.get("result").hidden, false);
  deferred = true;
  const pending = ui.submit("second");
  assert.equal(ui.get("result").hidden, true);
  next(reply(answer()));
  await pending;
  assert.equal(ui.get("submitted-question").textContent, "second");
});

for (const [status, code, expected] of [
  [422, "invalid_request", /Check your question/], [503, "generation_unavailable", /temporarily unavailable/],
  [503, "evidence_context_limit", /could not fit/], [504, "request_timeout", /took too long/],
  [502, "invalid_provider_response", /could not be completed/], [500, "internal_error", /could not be completed/],
]) {
  test(`safe feedback for HTTP ${status} / ${code}`, async () => {
    const ui = setup(async () => reply({ error: { code, message: "PRIVATE_DB_PASSWORD=never-expose" } }, status));
    await ui.submit("q");
    assert.match(ui.get("request-error").textContent, expected);
    assert.ok(!ui.get("request-error").textContent.includes("never-expose"));
    assert.equal(ui.get("result").hidden, true);
    assert.equal(ui.get("submit-question").disabled, false);
  });
}

test("network errors are safe and allow another request", async () => {
  let fail = true;
  const ui = setup(async () => { if (fail) throw new TypeError("secret network details"); return reply(answer()); });
  await ui.submit("q");
  assert.match(ui.get("request-error").textContent, /Could not reach the server/);
  assert.ok(!ui.get("request-error").textContent.includes("secret"));
  fail = false;
  await ui.submit("retry");
  assert.equal(ui.get("result").hidden, false);
  assert.equal(ui.get("request-error").hidden, true);
});

test("client timeout aborts once, never auto-retries, and restores interaction", async () => {
  const ui = setup((url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")));
  }));
  const pending = ui.submit("q");
  [...ui.timers.values()][0]();
  await pending;
  assert.match(ui.get("request-error").textContent, /may still be finishing/);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.get("submit-question").disabled, false);
});

for (const data of [null, { status: "unknown", citations: [] }, answer([{}]),
  { status: "insufficient_evidence", answer: "invented", citations: [] }]) {
  test(`malformed success payload is a safe error (${JSON.stringify(data)})`, async () => {
    const ui = setup(async () => reply(data));
    await ui.submit("q");
    assert.match(ui.get("request-error").textContent, /unusable answer/);
    assert.equal(ui.get("result").hidden, true);
    assert.equal(ui.get("sources").hidden, true);
  });
}

test("HTML/non-JSON error bodies never reach the DOM", async () => {
  const ui = setup(async () => ({ ok: false, status: 500, json: async () => { throw new SyntaxError("<script>secret</script>"); } }));
  await ui.submit("q");
  assert.match(ui.get("request-error").textContent, /could not be completed/);
  assert.ok(!ui.get("request-error").textContent.includes("secret"));
});
