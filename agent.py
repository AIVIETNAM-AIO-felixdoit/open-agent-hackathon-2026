"""Multi-agent due-diligence system.

Planner -> Retriever -> Analyst -> Critic, with a feedback loop: the Critic can reject
unsupported claims and send the Planner back for more evidence. Every agent-to-agent
message is written to a trace file (traces/<run_id>.jsonl) and streamed to the UI.
"""
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import AsyncOpenAI

from hmem import HMem

load_dotenv()

# All model calls go through one OpenAI-compatible gateway (point LLM_BASE_URL at Meterless).
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=os.getenv("LLM_API_KEY", "missing"))
llm_slots = asyncio.Semaphore(4)  # keep concurrent API calls polite

TAVILY_KEY = os.getenv("TAVILY_API_KEY")
TRACE_DIR = Path(__file__).parent / "logs"

MAX_ROUNDS = 2  # Critic may send work back once
RESULTS_PER_QUERY = 3
PAGE_CHARS = 5000
DIMENSIONS = ["Financial health", "Leadership & governance", "Legal & regulatory",
              "Market & competition", "Reputation & news", "Product & technology"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


class Trace:
    """Records agent interactions; each event is persisted and handed to the UI stream."""

    def __init__(self):
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        TRACE_DIR.mkdir(exist_ok=True)
        self.path = TRACE_DIR / f"trace_{self.run_id}.jsonl"
        self.queue: asyncio.Queue = asyncio.Queue()

    def log(self, agent: str, action: str, to: str = "", **data):
        event = {"type": "trace", "t": time.strftime("%H:%M:%S"), "agent": agent, "to": to,
                 "action": action, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.queue.put_nowait(event)


async def ask_json(trace: Trace, agent: str, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Call the model for one agent and parse a JSON object from its reply (one retry on bad JSON)."""
    for attempt in range(2):
        started = time.time()
        async with llm_slots:
            resp = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": "/no_think\n" + system + "\nReply with valid JSON only."},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
        usage = resp.usage
        trace.log(agent, "model_call", model=LLM_MODEL, gateway=LLM_BASE_URL,
                  seconds=round(time.time() - started, 1),
                  tokens=usage.total_tokens if usage else None)
        text = resp.choices[0].message.content or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        match = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(match.group(0))
        except (AttributeError, json.JSONDecodeError):
            if attempt == 1:
                raise ValueError(f"{agent}: model did not return JSON: {text[:300]}")


# ---------- tools ----------

async def web_search(query: str) -> list[dict]:
    """Return [{title, url, snippet, text}] from Tavily (page text included)."""
    if not TAVILY_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {TAVILY_KEY}"},
                json={"query": query, "max_results": RESULTS_PER_QUERY, "include_raw_content": True},
            )
            r.raise_for_status()
        return [
            {"title": x.get("title") or x.get("url"), "url": x.get("url"), "snippet": x.get("content", ""),
             "text": re.sub(r"\s+", " ", x.get("raw_content") or "")[:PAGE_CHARS]}
            for x in r.json().get("results", [])
        ]
    except Exception:
        return []


async def read_page(http: httpx.AsyncClient, url: str) -> str:
    try:
        r = await http.get(url, timeout=10)
        if "html" not in r.headers.get("content-type", ""):
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ")).strip()[:PAGE_CHARS]
    except Exception:
        return ""


# ---------- agents ----------

PLANNER_PROMPT = f"""You are the Planner in a due-diligence team. Turn the question into hypotheses to test and
web-search queries that would confirm or refute them. Cover these dimensions: {", ".join(DIMENSIONS)}.
If the Critic sent feedback, plan ONLY the follow-up work it asks for.
Return {{"hypotheses": ["..."], "queries": [{{"dimension": "...", "query": "...", "why": "which hypothesis it tests"}}]}}
with at most 6 queries, each including the company name."""

EXTRACT_PROMPT = f"""You are the Analyst's evidence extractor. From the source text, pull at most 4 factual claims
relevant to the question. Each claim must be backed by an exact short quote from the text.
Return {{"claims": [{{"claim": "...", "quote": "...", "dimension": "<one of: {", ".join(DIMENSIONS)}>",
"signal": "positive|negative|neutral", "date": "date mentioned or empty"}}]}}.
If the text is irrelevant or unreadable, return {{"claims": []}}. Never invent facts."""

ANALYST_PROMPT = """You are the Analyst writing a due-diligence decision memo. Use ONLY the numbered evidence.
Every reason, risk and contradiction must cite evidence ids like "E3". Be explicit about uncertainty.
If the Critic rejected claims, remove or fix them.
Return JSON:
{"verdict": "Proceed|Proceed with caution|Do not proceed|Insufficient evidence",
 "summary": "2-3 sentence answer to the question",
 "confidence": <0-100>,
 "confidence_reason": "why this confidence (source quality, agreement, recency, gaps)",
 "reasons": [{"point": "...", "evidence": ["E1"]}],
 "risks": [{"risk": "...", "severity": "high|medium|low", "evidence": ["E2"]}],
 "contradictions": [{"issue": "what sources disagree on", "evidence": ["E4", "E7"]}],
 "gaps": ["important things the evidence did not cover"],
 "next_steps": ["concrete checks a human should do next"]}"""

CRITIC_PROMPT = f"""You are the Critic. Audit the Analyst's draft memo against the evidence list.
1. For each reason/risk/contradiction, check the cited evidence really supports it. List any that do not.
2. Check which of these dimensions have no or weak evidence: {", ".join(DIMENSIONS)}.
3. Check the verdict and confidence are justified by the evidence.
Return {{"approved": true|false,
 "unsupported_claims": [{{"claim": "...", "problem": "why the cited evidence does not support it"}}],
 "missing_dimensions": ["..."],
 "follow_up": ["specific things the Planner should research next"],
 "comment": "one-sentence verdict on the draft"}}
Approve only if there are no unsupported claims and no critical dimension is missing."""


async def planner(trace, mem, company, task, feedback=None):
    user = task if not feedback else f"{task}\n\nCritic feedback to address:\n{json.dumps(feedback, ensure_ascii=False)}"
    recalled = mem.retrieve(f"{task} gaps lessons research plan", agent="Planner", entities=[company],
                            tags=["procedural", "finding"])
    if recalled["memories"]:
        user += "\n\n" + HMem.as_prompt_block(recalled) + \
                "\nUse these to avoid repeating searches that failed before and to target known gaps."
    plan = await ask_json(trace, "Planner", PLANNER_PROMPT, user, max_tokens=900)
    plan["queries"] = plan.get("queries", [])[:6]
    trace.log("Planner", "plan", to="Retriever", hypotheses=plan.get("hypotheses", []), queries=plan["queries"])
    return plan


async def retriever(trace, queries, known_urls):
    results = await asyncio.gather(*(web_search(q["query"]) for q in queries))
    sources = []
    for q, hits in zip(queries, results):
        trace.log("Retriever", "tool_call", tool="web_search", query=q["query"], hits=len(hits))
        for hit in hits:
            if hit["url"] and hit["url"] not in known_urls:
                known_urls.add(hit["url"])
                sources.append(dict(hit, dimension=q.get("dimension", "")))
    missing = [s for s in sources if not s["text"]]
    async with httpx.AsyncClient(headers=UA, follow_redirects=True) as http:
        pages = await asyncio.gather(*(read_page(http, s["url"]) for s in missing))
    for s, text in zip(missing, pages):
        s["text"] = text or s["snippet"]
    trace.log("Retriever", "sources", to="Analyst", count=len(sources),
              sources=[{"title": s["title"], "url": s["url"]} for s in sources])
    return sources


async def analyst_extract(trace, task, sources):
    async def extract(s):
        try:
            out = await ask_json(trace, "Analyst", EXTRACT_PROMPT,
                                 f"{task}\n\nSource ({s['url']}):\n{s['text']}", max_tokens=900)
            return [dict(c, source=s["id"]) for c in out.get("claims", []) if c.get("claim")]
        except Exception:
            return []
    return [c for claims in await asyncio.gather(*(extract(s) for s in sources)) for c in claims]


def evidence_lines(evidence):
    return "\n".join(
        f'{e["id"]} [{e["source"]}] ({e.get("dimension", "")}, {e.get("signal", "neutral")}'
        f'{", " + e["date"] if e.get("date") else ""}): {e["claim"]} | quote: "{e.get("quote", "")}"'
        for e in evidence
    )


async def analyst_draft(trace, mem, company, task, evidence, critique=None):
    user = f"{task}\n\nEvidence:\n{evidence_lines(evidence)}"
    recalled = mem.retrieve(task, agent="Analyst", entities=[company], tags=["finding"])
    prior = [m for m in recalled["memories"] if m["runId"] != mem.run_id]
    if prior:
        user += "\n\n" + HMem.as_prompt_block({"memories": prior}) + \
                "\nThese are conclusions from earlier investigations. Note in the summary if current evidence changes them."
    if critique:
        user += f"\n\nCritic rejected your previous draft:\n{json.dumps(critique, ensure_ascii=False)}"
    memo = await ask_json(trace, "Analyst", ANALYST_PROMPT, user, max_tokens=2500)
    trace.log("Analyst", "draft", to="Critic", verdict=memo.get("verdict"), confidence=memo.get("confidence"))
    return memo


async def critic(trace, task, evidence, memo):
    review = await ask_json(trace, "Critic", CRITIC_PROMPT,
                            f"{task}\n\nEvidence:\n{evidence_lines(evidence)}\n\nDraft memo:\n"
                            f"{json.dumps(memo, ensure_ascii=False)}", max_tokens=1200)
    trace.log("Critic", "review", to="Planner" if not review.get("approved") else "User",
              approved=bool(review.get("approved")), comment=review.get("comment", ""),
              unsupported_claims=review.get("unsupported_claims", []),
              missing_dimensions=review.get("missing_dimensions", []))
    return review


# ---------- orchestration ----------

async def run_investigation(trace: Trace, company: str, question: str) -> dict:
    question = question.strip() or f"Should we invest in or partner with {company}? What are the key risks?"
    task = f"Company: {company}\nQuestion: {question}"
    trace.log("User", "question", to="Planner", company=company, question=question)
    mem = HMem(trace.run_id, trace)

    sources, evidence, known_urls = [], [], set()
    feedback, memo, review = None, None, None
    for round_no in range(1, MAX_ROUNDS + 1):
        trace.log("Orchestrator", "round", round=round_no)
        plan = await planner(trace, mem, company, task, feedback)
        new_sources = await retriever(trace, plan["queries"], known_urls)
        for s in new_sources:
            s["id"] = f"S{len(sources) + 1}"
            sources.append(s)
        for c in await analyst_extract(trace, task, new_sources):
            c["id"] = f"E{len(evidence) + 1}"
            evidence.append(c)
            # Mine each evidence item into shared short-term memory with provenance.
            c["memory_id"] = mem.add(c["claim"], agent="Analyst", layer="short_term", tags=[c.get("dimension", "")],
                                     entities=[company], source=c["source"], task_id=c["id"])["id"]
        trace.log("Analyst", "evidence", count=len(evidence))
        if not evidence:
            raise RuntimeError("No usable evidence found. Try a more specific company name.")

        memo = await analyst_draft(trace, mem, company, task, evidence, critique=review if round_no > 1 else None)
        review = await critic(trace, task, evidence, memo)
        # Critic verdicts become procedural memory so future runs plan around known gaps.
        for dim in review.get("missing_dimensions", []):
            mem.add(f"{company}: web sources gave weak evidence on {dim}; target filings or regulators directly.",
                    agent="Critic", mtype="procedural", layer="long_term", tags=["procedural"], entities=[company])
        if review.get("approved"):
            break
        if round_no < MAX_ROUNDS:
            feedback = {"missing_dimensions": review.get("missing_dimensions", []),
                        "follow_up": review.get("follow_up", [])}
            trace.log("Critic", "send_back", to="Planner", feedback=feedback)
        else:
            # Out of rounds: Analyst must drop what the Critic could not accept.
            memo = await analyst_draft(trace, mem, company, task, evidence, critique=review)
            memo.setdefault("gaps", []).append("Critic did not fully approve; some areas remain under-evidenced.")

    # Evidence cited by the final memo was useful: reinforce it and promote it to long-term memory.
    by_id = {e["id"]: e for e in evidence}
    cited = {eid for part in ("reasons", "risks", "contradictions") for item in memo.get(part, [])
             for eid in item.get("evidence", [])}
    for eid in cited & by_id.keys():
        mem.feedback(by_id[eid]["memory_id"], "helpful", "Critic")
        mem.promote(by_id[eid]["memory_id"], "Orchestrator")
    mem.add(f"{company} ({time.strftime('%Y-%m-%d')}): verdict {memo.get('verdict')} at {memo.get('confidence')}% "
            f"confidence. {memo.get('summary', '')}", agent="Analyst", mtype="factual", layer="long_term",
            tags=["finding"], entities=[company], derived_from=[by_id[e]["memory_id"] for e in cited & by_id.keys()])
    trace.log("H-MEM", "consolidate", promoted=len(cited & by_id.keys()))

    trace.log("Orchestrator", "done", verdict=memo.get("verdict"), confidence=memo.get("confidence"))
    return {
        "run_id": trace.run_id,
        "memo": memo,
        "critic": review,
        "evidence": evidence,
        "sources": [{k: s[k] for k in ("id", "title", "url", "dimension")} for s in sources],
    }


async def investigate(company: str, question: str):
    """Run the investigation, yielding trace events and finally the result (for streaming UIs)."""
    trace = Trace()
    job = asyncio.create_task(run_investigation(trace, company, question))
    while not job.done() or not trace.queue.empty():
        try:
            yield await asyncio.wait_for(trace.queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            pass
    try:
        yield {"type": "result", **job.result()}
    except Exception as exc:
        trace.log("Orchestrator", "error", message=str(exc))
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
