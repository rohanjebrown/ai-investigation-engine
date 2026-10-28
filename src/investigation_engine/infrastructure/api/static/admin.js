"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const auth = byId("admin-auth-form");
  const tokenInput = byId("admin-token");
  const workspace = byId("admin-workspace");
  const status = byId("admin-status");
  const list = byId("document-list");
  let bearer = null; // Deliberately memory-only. Never write this value to browser storage.
  let retryKey = null;
  let retryTarget = null;
  globalThis.investigationAdminAuthorization = () => bearer ? `Bearer ${bearer}` : null;

  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  }

  async function call(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${bearer}`);
    const response = await fetch(path, { ...options, headers });
    let body;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) {
      const detail = body?.detail;
      throw Object.assign(new Error(detail?.message || "Request failed"), { status: response.status, code: detail?.code });
    }
    return body;
  }

  async function refresh() {
    const page = await call("/documents?limit=50&offset=0");
    list.replaceChildren();
    for (const item of page.items || []) {
      const document = item.document;
      const current = item.current_version;
      const isShared = document.owner_id === null;
      const card = node("article", undefined, "document-item");
      card.append(node("h3", document.title || "Untitled document"));
      card.append(node("p", `ID: ${document.id} · ${isShared ? "shared; system managed" : "private to administrator"} · versions: ${item.version_count} · ${document.deleted_at ? "deleted" : current ? "active version searchable" : "not searchable yet"}`));
      const actions = node("div");
      const inspect = node("button", "Inspect");
      inspect.type = "button";
      inspect.addEventListener("click", async () => {
        try {
          const details = await call(`/documents/${encodeURIComponent(document.id)}`);
          const pre = node("pre", JSON.stringify(details, null, 2));
          const prior = card.querySelector("pre");
          if (prior) prior.remove();
          card.append(pre);
          byId("replace-id").value = isShared ? "" : document.id;
          byId("replace-version").value = isShared ? "" : document.current_version_id || "";
          status.textContent = `Index state: ${details.indexing?.state || "unknown"}`;
        } catch (error) { status.textContent = safeMessage(error); }
      });
      const index = node("button", "Index / resume");
      index.type = "button";
      index.addEventListener("click", async () => {
        try {
          const result = await call(`/documents/${encodeURIComponent(document.id)}/index`, { method: "POST" });
          status.textContent = `${result.status}: ${result.progress.indexed_embeddings}/${result.progress.eligible_embeddings ?? "?"} embeddings`;
          await refresh();
        } catch (error) { status.textContent = safeMessage(error); }
      });
      const remove = node("button", "Delete");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Soft-delete this document? Its history will be retained.")) return;
        try { await call(`/documents/${encodeURIComponent(document.id)}`, { method: "DELETE" }); status.textContent = "Document deleted."; await refresh(); }
        catch (error) { status.textContent = safeMessage(error); }
      });
      if (isShared) { index.disabled = true; remove.disabled = true; }
      actions.append(inspect, index, remove);
      card.append(actions);
      list.append(card);
    }
  }

  function safeMessage(error) {
    if (error.status === 401 || error.status === 503) return "Authentication failed or document management is disabled.";
    if (error.status === 409) return "The document changed or has a pending replacement. Refresh and inspect it.";
    if (error.status === 413) return "The file or extracted document exceeds configured limits.";
    return error.message || "Document operation failed.";
  }

  auth.addEventListener("submit", async (event) => {
    event.preventDefault();
    bearer = tokenInput.value;
    tokenInput.value = "";
    workspace.hidden = false;
    byId("admin-logout").hidden = false;
    byId("question-scope").textContent = "Shared + administrator documents";
    byId("question-intro").textContent = "Get an answer from shared and your private documents, then inspect the source excerpts behind it.";
    try { await refresh(); status.textContent = "Authenticated. Token remains only in this page's memory."; }
    catch (error) {
      bearer = null;
      byId("question-scope").textContent = "Shared documents only";
      byId("question-intro").textContent = "Get an answer from the shared documents, then inspect the source excerpts behind it.";
      workspace.hidden = true;
      byId("admin-logout").hidden = true;
      status.textContent = safeMessage(error);
    }
  });

  byId("admin-logout").addEventListener("click", () => {
    bearer = null;
    byId("question-scope").textContent = "Shared documents only";
    byId("question-intro").textContent = "Get an answer from the shared documents, then inspect the source excerpts behind it.";
    list.replaceChildren();
    workspace.hidden = true;
    byId("admin-logout").hidden = true;
    status.textContent = "Signed out.";
  });
  byId("refresh-documents").addEventListener("click", () => refresh().then(() => { status.textContent = "Document list refreshed."; }).catch((error) => { status.textContent = safeMessage(error); }));
  byId("upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = byId("document-file").files[0];
    if (!file) { status.textContent = "Select a document file."; return; }
    const id = byId("replace-id").value.trim();
    const version = byId("replace-version").value.trim();
    const target = `${id}:${byId("replace-version").value.trim()}:${file.name}:${file.size}:${file.lastModified}`;
    if (retryTarget !== target) { retryTarget = target; retryKey = crypto.randomUUID(); }
    const key = retryKey;
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const result = await call(id ? `/documents/${encodeURIComponent(id)}` : "/documents", {
        method: id ? "PUT" : "POST", headers: { "Idempotency-Key": key, ...(id && version ? { "If-Match": `"${version}"` } : {}) }, body: form,
      });
      retryKey = null;
      retryTarget = null;
      status.textContent = `${result.status}. Original file bytes are not retained. Indexing must be started explicitly.`;
      await refresh();
    } catch (error) { status.textContent = safeMessage(error); }
  });
})();
