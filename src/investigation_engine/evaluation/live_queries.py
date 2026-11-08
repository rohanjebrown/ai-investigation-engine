"""Exact compatible query-cache reuse, with explicitly authorized bounded misses."""

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from investigation_engine.application.embeddings import storage_vector
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings, CachedQueryMiss
from investigation_engine.evaluation.reporting import write_json
from investigation_engine.evaluation.telemetry import _current


class ReadThroughQueryEmbeddings(CachedQueryEmbeddings):
    def __init__(self, configuration, vectors, *, cache_path, live=None, allow_live=False):
        super().__init__(configuration, vectors)
        if live is not None and (not allow_live or live.configuration != configuration):
            raise ValueError("Live query embeddings require authorization and an identical embedding profile")
        if allow_live and live is None:
            raise ValueError("Authorized live query embeddings require a configured provider")
        self.live, self.cache_path = live, Path(cache_path)
        self.hits = self.misses = 0

    @property
    def request_counts(self):
        return self.live.request_counts if self.live else {"count_tokens": 0, "embed_content": 0}

    async def embed_query(self, question):
        started = perf_counter()
        cached = question in self._vectors
        self.hits += int(cached)
        self.misses += int(not cached)
        before = dict(self.request_counts)
        api_names = ("gemini.embedding.count_tokens_api", "gemini.embedding.embed_content_api")
        item = {"query": question, "cache_hit": cached, "status": "failed"}
        observation = _current.get()
        before_stages = {key: len(observation.stages[key]) for key in api_names} if observation else {}
        if observation is not None:
            observation.embeddings.append(item)
        try:
            if cached:
                result = self._vectors[question]
            else:
                if self.live is None:
                    raise CachedQueryMiss("No offline query vector for the exact question")
                self.live.budget.require(2)  # Both token counting and embedding must fit.
                result = storage_vector(await self.live.embed_query(question))
                # Re-read before atomic replacement to preserve compatible vectors
                # imported since startup; reject visible incompatible changes.
                value = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if value["configuration"] != asdict(self.configuration):
                    raise ValueError("Query cache profile changed during evaluation")
                vectors = value["vectors"]
                for query, existing in self._vectors.items():
                    if query in vectors and storage_vector(vectors[query]) != existing:
                        raise ValueError("Query cache vector changed during evaluation")
                    vectors.setdefault(query, list(existing))
                if question in vectors and storage_vector(vectors[question]) != result:
                    raise ValueError("Conflicting concurrent query embedding")
                vectors[question] = list(result)
                write_json(self.cache_path, value)
                self._vectors[question] = result
            item.update(status="ok", vector_sha256=sha256(json.dumps(result).encode()).hexdigest())
            return result
        finally:
            item["duration_ms"] = (perf_counter() - started) * 1000
            after = self.request_counts
            item["provider_requests"] = {key: after[key] - before[key] for key in before}
            api_values = (
                [value for key, count in before_stages.items() for value in observation.stages[key][count:]]
                if observation
                else []
            )
            item["api_duration_ms"] = sum(api_values) if api_values else None
            item["counted_input_tokens"] = (
                self.live.query_tokens
                if self.live and after["count_tokens"] > before["count_tokens"] and self.live.query_tokens
                else None
            )
