"use strict";

// Dependency-free real Chromium/Edge CDP check. The caller starts a local API
// with a replay of a real live investigation response; no new Gemini calls.
const assert = require("node:assert/strict");

const cdpPort = Number(process.argv[2]);
const baseUrl = process.argv[3];
const question = process.argv[4];

async function main() {
  let target;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
      const pages = await response.json();
      target = pages.find((item) => item.type === "page");
      if (target) break;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(target?.webSocketDebuggerUrl, "headless browser did not expose a page target");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let nextId = 0;
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    const resolve = pending.get(message.id);
    if (resolve) {
      pending.delete(message.id);
      if (message.error) resolve(Promise.reject(new Error(message.error.message)));
      else resolve(message.result);
    }
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, (value) => value instanceof Error ? reject(value) : resolve(value));
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await command("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser evaluation failed");
    return result.result.value;
  };
  try {
    await command("Page.enable");
    await command("Runtime.enable");
    await command("Page.navigate", { url: `${baseUrl}/` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await evaluate("document.readyState === 'complete' && !!document.getElementById('question-mode')")) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const expression = `(() => {
      const mode = document.getElementById('question-mode');
      mode.value = 'investigation';
      document.getElementById('question').value = ${JSON.stringify(question)};
      document.getElementById('question-form').dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}));
      return true;
    })()`;
    assert.equal(await evaluate(expression), true);
    let ready = false;
    for (let attempt = 0; attempt < 200; attempt += 1) {
      ready = await evaluate("!document.getElementById('result').hidden && document.getElementById('answer-status').textContent === 'Answer generated'");
      if (ready) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.ok(ready, "browser did not render a successful investigation response");
    const before = await evaluate(`({
      answer: document.getElementById('answer-text').textContent,
      steps: document.getElementById('search-list').textContent,
      count: document.getElementById('step-count').textContent,
      endpoint: performance.getEntriesByType('resource').map((item) => item.name).find((name) => name.endsWith('/investigations'))
    })`);
    assert.match(before.count, /\d+ searches, \d+ model turns/);
    assert.match(before.steps, /Step 1:/);
    assert.match(before.endpoint, /\/investigations$/);
    assert.ok(before.answer.length > 0);
    await evaluate("document.querySelector('#citation-links button').click()");
    const after = await evaluate(`({
      sourcesHidden: document.getElementById('sources').hidden,
      open: document.querySelector('#source-list details').open,
      excerpt: document.querySelector('#source-list blockquote').textContent,
      citations: document.getElementById('citation-links').textContent
    })`);
    assert.equal(after.sourcesHidden, false);
    assert.equal(after.open, true);
    assert.ok(after.excerpt.length > 0 && after.citations.includes("E"));
    console.log(JSON.stringify({ ...before, citationExpanded: after.open, excerpt: after.excerpt }));
  } finally {
    socket.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
