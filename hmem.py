"""Meterless H-MEM Agent Engine (minimal): MemoryStoreService + MemoryRetrievalService + TrustLedger.

Tiered memory (short_term / working / long_term) shared by all agents, with provenance and
agent attribution. Long-term memories persist across runs in memory/long_term.jsonl, so a later
investigation of the same company warm-starts with what earlier runs learned. Every mutation and
retrieval is written as JSONL to logs/ as evidence.
"""
import json
import math
import re
import time
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
MEMORY_DIR = ROOT / "memory"
LOG_DIR = ROOT / "logs"

LAYER_WEIGHT = {"long_term": 1.0, "working": 0.85, "short_term": 0.7}
TOP_N = 5
THRESHOLD = 0.35
STOP = set("the a an of to in on for and or is are was were be by with at from as that this it its "
           "their has have had will would should can could not no yes what which who how why".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2]


def embed(text: str) -> dict[str, float]:
    """Lightweight bag-of-words embedding (no model needed, works in sample mode)."""
    counts = Counter(tokens(text))
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items()}


def cosine(a: dict, b: dict) -> float:
    return sum(v * b.get(k, 0.0) for k, v in a.items())


class HMem:
    def __init__(self, run_id: str, trace=None):
        self.run_id = run_id
        self.trace = trace
        self.memories: dict[str, dict] = {}
        MEMORY_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)
        self.long_term_path = MEMORY_DIR / "long_term.jsonl"
        self.ledger_path = LOG_DIR / f"hmem_ledger_{run_id}.jsonl"
        self.retrieval_path = LOG_DIR / f"hmem_retrieval_{run_id}.jsonl"
        self._hydrate()

    # ----- TrustLedgerService -----
    def _audit(self, action: str, actor: str, memory_id: str = "", **details):
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "runId": self.run_id, "action": action,
                 "actor": actor, "memoryId": memory_id, **details}
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ----- MemoryStoreService -----
    def _hydrate(self):
        """Warm start: load long-term memories written by previous runs."""
        if self.long_term_path.exists():
            for line in self.long_term_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    m = json.loads(line)
                    self.memories[m["id"]] = m
        self._audit("hydrate", "MemoryStoreService", count=len(self.memories))
        if self.trace:
            self.trace.log("H-MEM", "hydrate", long_term_memories=len(self.memories))

    def add(self, content: str, *, agent: str, mtype: str = "factual", layer: str = "short_term",
            tags: list[str] | None = None, entities: list[str] | None = None, source: str = "",
            confidence: float = 0.7, derived_from: list[str] | None = None, task_id: str = "") -> dict:
        emb = embed(content)
        # De-duplicate: a near-identical memory is reinforced instead of duplicated.
        for m in self.memories.values():
            if cosine(emb, m["embedding"]) > 0.92:
                m["accessCount"] += 1
                m["confidence"] = min(1.0, m["confidence"] + 0.05)
                self._audit("reinforce", agent, m["id"])
                return m
        m = {
            "id": "M-" + uuid.uuid4().hex[:8], "content": content, "type": mtype, "layer": layer,
            "timestamp": time.time(), "lastAccessed": time.time(), "accessCount": 0,
            "tags": [t.lower() for t in (tags or [])], "entities": [e.lower() for e in (entities or [])],
            "confidence": confidence, "source": source, "derivedFrom": derived_from or [],
            "supersedes": None, "agentId": agent, "runId": self.run_id, "taskId": task_id,
            "embedding": emb,
        }
        self.memories[m["id"]] = m
        self._audit("create", agent, m["id"], layer=layer, type=mtype, content=content[:200])
        if layer == "long_term":
            self._persist(m)
        return m

    def promote(self, memory_id: str, actor: str):
        m = self.memories.get(memory_id)
        if m and m["layer"] != "long_term":
            m["layer"] = "long_term"
            self._persist(m)
            self._audit("promote", actor, memory_id)

    def feedback(self, memory_id: str, verdict: str, actor: str):
        """Critic feedback adapts confidence: helpful / not_helpful / wrong."""
        m = self.memories.get(memory_id)
        if not m:
            return
        delta = {"helpful": 0.1, "not_helpful": -0.05, "wrong": -0.3}.get(verdict, 0)
        m["confidence"] = max(0.0, min(1.0, m["confidence"] + delta))
        if verdict == "helpful":
            m["accessCount"] += 1
        if verdict == "wrong":
            m["tags"].append("needs_review")
        self._audit("feedback", actor, memory_id, verdict=verdict, confidence=round(m["confidence"], 2))

    def _persist(self, m: dict):
        with self.long_term_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # ----- MemoryRetrievalService -----
    def retrieve(self, query: str, *, agent: str, entities: list[str] | None = None,
                 tags: list[str] | None = None, top_n: int = TOP_N) -> dict:
        q_emb, q_kw = embed(query), set(tokens(query))
        ents = {e.lower() for e in (entities or [])}
        q_tags = {t.lower() for t in (tags or [])}
        scored = []
        for m in self.memories.values():
            kw = len(q_kw & set(tokens(m["content"]))) / (len(q_kw) or 1)
            score = (0.5 * cosine(q_emb, m["embedding"]) + 0.2 * kw
                     + 0.1 * bool(q_tags & set(m["tags"])) + 0.25 * bool(ents & set(m["entities"])))
            score *= LAYER_WEIGHT[m["layer"]] * (0.6 + 0.4 * m["confidence"])
            if m["runId"] == self.run_id:
                score += 0.05  # agent-scope boost: same orchestration run
            if score >= THRESHOLD * 0.5:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = scored[:top_n]
        for _, m in picked:
            m["lastAccessed"], m["accessCount"] = time.time(), m["accessCount"] + 1
        payload = {
            "strategy": "task_focused",
            "retrievalReason": query[:160],
            "memories": [{"id": m["id"], "content": m["content"], "layer": m["layer"],
                          "agentId": m["agentId"], "runId": m["runId"], "confidence": round(m["confidence"], 2),
                          "relevance": round(s, 3)} for s, m in picked],
        }
        with self.retrieval_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), "agent": agent, **payload}, ensure_ascii=False) + "\n")
        self._audit("read", agent, count=len(picked))
        if self.trace:
            self.trace.log("H-MEM", "retrieve", to=agent, count=len(picked),
                           from_previous_runs=sum(1 for m in payload["memories"] if m["runId"] != self.run_id))
        return payload

    @staticmethod
    def as_prompt_block(payload: dict) -> str:
        if not payload["memories"]:
            return ""
        lines = [f'- [{m["id"]}, {m["layer"]}, by {m["agentId"]}, conf {m["confidence"]}] {m["content"]}'
                 for m in payload["memories"]]
        return "Relevant shared memory (H-MEM):\n" + "\n".join(lines)
