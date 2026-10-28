"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const form = byId("question-form");
  const question = byId("question");
  const submit = byId("submit-question");
  const status = byId("request-status");
  const error = byId("request-error");
  const result = byId("result");
  const sources = byId("sources");
  const mode = byId("question-mode");
  const endpoint = () => mode.value === "investigation" ? "/investigations" : "/questions";
  let busy = false;

  // Never interpret generated answers, excerpts, titles or metadata as HTML.
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function labelFor(evidence) {
    const candidates = [evidence.title, evidence.source.display_name, evidence.source.metadata?.title];
    return candidates.find((value) => typeof value === "string" && value.trim()) || "Source document";
  }

  function addField(list, name, value) {
    if (value !== null && value !== undefined && value !== "") {
      list.append(element("dt", name), element("dd", String(value)));
    }
  }

  function addObject(body, heading, value) {
    if (value && Object.keys(value).length) {
      body.append(element("h3", heading), element("pre", JSON.stringify(value, null, 2)));
    }
  }

  function sourceCard(citation) {
    const evidence = citation.evidence;
    const source = evidence.source;
    const label = `[${citation.evidence_id}] ${labelFor(evidence)}`;
    const card = element("details", undefined, "source-card");
    const summary = element("summary", label);
    const body = element("div", undefined, "source-body");
    body.append(element("blockquote", evidence.text, "excerpt"));
    const fields = element("dl");
    addField(fields, "Evidence reference", citation.evidence_id);
    addField(fields, "Document ID", evidence.document_id);
    addField(fields, "Version ID", evidence.document_version_id);
    addField(fields, "Canonical range", `[${evidence.start}, ${evidence.end}) characters`);
    addField(fields, "Content SHA-256", evidence.content_sha256);
    addField(fields, "Source kind", source.kind);
    addField(fields, "Source ID", source.source_id);
    addField(fields, "Source revision", source.revision);
    if (evidence.title && source.display_name !== evidence.title) addField(fields, "Display name", source.display_name);
    body.append(fields);
    addObject(body, "Original record locator", source.record_locator);
    addObject(body, "Descriptive source metadata", source.metadata);
    if (evidence.source_spans.length) {
      body.append(element("h3", "Original source locations"));
      const locations = element("ul");
      for (const span of evidence.source_spans) {
        const locators = span.locators.map((locator) => `${locator.kind} index ${locator.index}`).join(" · ");
        locations.append(element("li", `${locators} — original text range [${span.start}, ${span.end})`));
      }
      body.append(locations, element("p", "All indices and character ranges are zero-based; range ends are excluded. Original source spans may extend beyond the retrieved excerpt.", "location-note"));
    } else {
      body.append(element("p", "No original page, sentence or line locations were supplied.", "location-note"));
    }
    card.append(summary, body);
    const button = element("button", label, "citation-button");
    button.type = "button";
    button.addEventListener("click", () => {
      card.open = true;
      summary.focus();
      card.scrollIntoView({ block: "nearest" });
    });
    return { card, button };
  }

  function render(data, submittedQuestion) {
    // Transport-shape guard only; business/citation validation stays on the server.
    const isInvestigation = submittedQuestion.mode === "investigation";
    if (!data || !Array.isArray(data.citations) || !(isInvestigation
      ? ["answered", "insufficient_evidence", "conflicting_evidence", "incomplete"].includes(data.status)
      : ["answered", "insufficient_evidence"].includes(data.status))) {
      throw new Error("Invalid response shape");
    }
    if (data.status === "answered" && (typeof data.answer !== "string" || !data.answer.trim() || !data.citations.length)) {
      throw new Error("Missing answer");
    }
    if (data.status !== "answered" && data.status !== "conflicting_evidence" && data.answer !== null) {
      throw new Error("Invalid abstention shape");
    }
    const evidenceById = new Map((data.evidence || []).map((item) => [item.evidence_id, item.evidence]));
    let citationIds = data.citations;
    if (isInvestigation && data.status === "conflicting_evidence") {
      citationIds = [...new Set((data.conflicts || []).flatMap((claim) => claim.citations || []))];
    }
    const citations = isInvestigation ? citationIds.map((id) => {
      const evidence = evidenceById.get(id);
      if (!evidence) throw new Error("Unknown citation");
      return { evidence_id: id, evidence };
    }) : data.citations;
    byId("submitted-question").textContent = submittedQuestion.value;
    const badge = byId("answer-status");
    const links = byId("citation-links");
    const list = byId("source-list");
    links.replaceChildren();
    list.replaceChildren();
    const steps = byId("investigation-steps");
    const searchList = byId("search-list");
    const conflictList = byId("conflict-list");
    searchList.replaceChildren();
    conflictList.replaceChildren();
    if (isInvestigation) {
      byId("step-count").textContent = `(${(data.searches || []).length} searches, ${data.generation_requests || 0} model turns)`;
      (data.searches || []).forEach((search) => {
        const summary = `Step ${search.sequence}: ${search.query} — ${search.succeeded ? `${search.result_count} results` : search.disposition}`;
        const item = element("li", summary);
        item.append(element("span", ` (${(search.evidence_ids || []).join(", ") || "no evidence IDs"})`));
        searchList.append(item);
      });
      for (const conflict of data.conflicts || []) {
        conflictList.append(element("p", `${conflict.claim} [${conflict.citations.join(", ")}]`));
      }
      steps.hidden = false;
    } else {
      steps.hidden = true;
    }
    if (data.status === "insufficient_evidence") {
      badge.textContent = "Insufficient evidence";
      badge.className = "badge insufficient";
      byId("answer-text").textContent = "The available documents do not establish an answer. Try a more specific question or check the scope of the shared corpus.";
      sources.hidden = true;
      status.textContent = "No answer established from the available evidence.";
    } else if (data.status === "incomplete" || data.status === "conflicting_evidence") {
      badge.textContent = data.status === "incomplete" ? "Investigation incomplete" : "Conflicting evidence";
      badge.className = "badge insufficient";
      const message = data.status === "incomplete"
        ? (data.message || "The investigation reached its resource limit before concluding.")
        : "The retrieved sources support incompatible claims; no single answer was selected.";
      byId("answer-text").textContent = message;
      if (data.status === "conflicting_evidence" && citations.length) {
        citations.forEach((citation) => {
          const { card, button } = sourceCard(citation);
          list.append(card);
          links.append(button);
        });
        byId("source-count").textContent = `${citations.length} conflicting sources`;
        sources.hidden = false;
      } else {
        sources.hidden = true;
      }
      status.textContent = message;
    } else {
      badge.textContent = "Answer generated";
      badge.className = "badge";
      byId("answer-text").textContent = data.answer;
      citations.forEach((citation) => {
        const { card, button } = sourceCard(citation);
        list.append(card);
        links.append(button);
      });
      byId("source-count").textContent = `${citations.length} ${citations.length === 1 ? "source" : "sources"}`;
      sources.hidden = false;
      status.textContent = "Answer ready. Select a citation to inspect its source.";
    }
    result.hidden = false;
  }

  function failureMessage(code, httpStatus) {
    if (code === "evidence_context_limit") return "The source excerpts could not fit this request. Try a more focused question.";
    if (httpStatus === 422) return "Check your question: enter non-whitespace text, up to 4,000 characters.";
    if (httpStatus === 504) return "The request took too long. Wait a moment before trying again.";
    if (httpStatus === 503) return "The answer service is temporarily unavailable. Please try again later.";
    return "The answer could not be completed. Please try again later.";
  }

  question.addEventListener("input", () => {
    byId("character-count").textContent = `${Array.from(question.value).length.toLocaleString("en-US")} / 4,000`;
    question.removeAttribute("aria-invalid");
    byId("question-error").hidden = true;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const value = question.value;
    if (!value.trim() || Array.from(value).length > 4000) {
      const validation = byId("question-error");
      validation.textContent = "Enter a question with non-whitespace text, up to 4,000 characters.";
      validation.hidden = false;
      question.setAttribute("aria-invalid", "true");
      question.focus();
      return;
    }
    busy = true;
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    const investigationMode = mode.value === "investigation";
    byId("submit-label").textContent = investigationMode ? "Investigating…" : "Finding an answer…";
    byId("loading-indicator").hidden = false;
    result.hidden = true;
    sources.hidden = true;
    error.hidden = true;
    byId("question-error").hidden = true;
    question.removeAttribute("aria-invalid");
    const scope = byId("question-scope").textContent;
    status.textContent = investigationMode
      ? `Investigating ${scope.toLowerCase()} with bounded follow-up searches…`
      : `Searching ${scope.toLowerCase()} and preparing an answer…`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), investigationMode ? 300000 : 100000);
    let received = false;
    try {
      const headers = { "Content-Type": "application/json" };
      if (typeof globalThis.investigationAdminAuthorization === "function") {
        const authorization = globalThis.investigationAdminAuthorization();
        if (authorization) headers.Authorization = authorization;
      }
      const response = await fetch(endpoint(), {
        method: "POST", headers,
        body: JSON.stringify({ question: value }), signal: controller.signal,
      });
      received = true;
      let data;
      try { data = await response.json(); } catch { data = null; }
      if (!response.ok) {
        error.textContent = failureMessage(data?.error?.code, response.status);
        error.hidden = false;
        status.textContent = "Request unsuccessful. You can try another question.";
      } else {
        render(data, { value, mode: mode.value });
      }
    } catch (failure) {
      result.hidden = true;
      sources.hidden = true;
      error.textContent = controller.signal.aborted
        ? "The request timed out. It may still be finishing; wait a moment before retrying."
        : !received
          ? "Could not reach the server. Check your connection and try again."
          : "The server returned an unusable answer. Please try again later.";
      error.hidden = false;
      status.textContent = "Request unsuccessful. You can try another question.";
    } finally {
      clearTimeout(timer);
      busy = false;
      submit.disabled = false;
      form.setAttribute("aria-busy", "false");
      byId("submit-label").textContent = "Find an answer";
      byId("loading-indicator").hidden = true;
    }
  });
})();
