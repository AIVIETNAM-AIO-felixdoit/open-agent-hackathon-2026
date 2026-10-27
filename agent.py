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
from json_repair import repair_json
from openai import AsyncOpenAI

from hmem import HMem
from zetaris_client import USL, Zetaris

load_dotenv()

# All model calls go through one OpenAI-compatible gateway (point LLM_BASE_URL at Meterless).
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=os.getenv("LLM_API_KEY", "missing"))
llm_slots = asyncio.Semaphore(4)  # keep concurrent API calls polite

TAVILY_KEY = os.getenv("TAVILY_API_KEY")
TRACE_DIR = Path(__file__).parent / "logs"
SAMPLE_DIR = Path(__file__).parent / "samples"
# No model key configured -> replay a recorded run instead of failing.
SAMPLE_MODE = os.getenv("SAMPLE_MODE", "").lower() in ("1", "true") or not os.getenv("LLM_API_KEY")

MAX_ROUNDS = 2  # Critic may send work back once
RESULTS_PER_QUERY = 3
PAGE_CHARS = 5000
SOURCE_CHARS = 1800  # per source when all sources share one extraction call
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
            resp = await client.with_options(max_retries=4, timeout=180).chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system + "\nReply with valid JSON only."},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
                # Nemotron 3 reasons by default; agents need the JSON answer, not the scratchpad.
                extra_body={"reasoning": {"enabled": False}},
            )
        usage = resp.usage
        trace.log(agent, "model_call", model=LLM_MODEL, gateway=LLM_BASE_URL,
                  seconds=round(time.time() - started, 1),
                  tokens=usage.total_tokens if usage else None)
        text = resp.choices[0].message.content or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        start = text.find("{")
        # json_repair also closes objects cut off by the token limit.
        parsed = repair_json(text[start:], return_objects=True) if start >= 0 else None
        if isinstance(parsed, dict) and parsed:
            return parsed
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


zetaris = Zetaris()

# Latest annual 10-K value per company/metric/period (later filings restate earlier years).
FINANCIALS_SQL = f"""SELECT ticker, entity_name, metric, period_end, value_usd, filed, accession FROM (
  SELECT c.ticker, c.entity_name, f.metric, f.period_end, f.value_usd, f.filed, f.accession,
         ROW_NUMBER() OVER (PARTITION BY f.cik, f.metric, f.period_end ORDER BY f.filed DESC) AS rn
  FROM {USL}.financial_fact f JOIN {USL}.company c ON c.cik = f.cik
  WHERE f.form = '10-K' AND f.period_end >= '2023-01-01'
    AND (f.period_start IS NULL OR datediff(to_date(f.period_end), to_date(f.period_start)) > 300)
) latest WHERE rn = 1 ORDER BY ticker, metric, period_end"""
PROFILE_SQL = f"""SELECT c.cik, c.entity_name, c.ticker, c.sic, s.description AS industry, s.major_group_desc,
       c.state_of_incorporation
FROM {USL}.company c JOIN {USL}.sic_code s ON s.sic_code = c.sic"""


async def zetaris_query(trace, sql: str, purpose: str) -> list[dict]:
    """Retriever tool: run Lightning SQL against the due-diligence USL in Zetaris."""
    started = time.time()
    rows = await zetaris.aquery(sql)
    trace.log("Retriever", "tool_call", tool="zetaris", purpose=purpose, sql=sql, rows=len(rows),
              seconds=round(time.time() - started, 1))
    return rows


def fmt_usd(v) -> str:
    v = float(v)
    return f"{v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"{v / 1e6:,.0f}M"


async def zetaris_financials(trace, company: str) -> list[dict]:
    """Structured SEC filing data from Zetaris, as one source the Analyst can cite like any web page."""
    try:
        profiles = await zetaris_query(trace, PROFILE_SQL, "company profiles + SIC industry (USL join)")
        target = next((p for p in profiles if company.lower() in p["entity_name"].lower()), None)
        if not target:
            trace.log("Retriever", "zetaris_skip", reason=f"{company} is not in the Zetaris USL")
            return []
        facts = await zetaris_query(trace, FINANCIALS_SQL, "annual 10-K financials, target vs peers (USL)")
        dq = await zetaris_query(trace, f"RUN DQ TABLE {USL}.financial_fact", "data-quality rules on financial_fact")
        trace.log("Retriever", "data_quality", rules=dq)
    except Exception as exc:
        trace.log("Retriever", "zetaris_error", message=str(exc)[:200])
        return []
    lines = [f"Source: SEC EDGAR 10-K XBRL filings, queried through Zetaris semantic layer {USL}. "
             f"Data-quality rules passed: " + ", ".join(f"{r['name']} {r['valid_record']}/{r['total_record']}" for r in dq)]
    for p in profiles:
        role = "TARGET" if p is target else "PEER"
        lines.append(f"{role} {p['entity_name']} ({p['ticker']}), SIC {p['sic']} {p['industry']}, "
                     f"incorporated in {p['state_of_incorporation']}.")
    series: dict[tuple, list[str]] = {}
    for f in facts:
        series.setdefault((f["ticker"], f["metric"]), []).append(f"FY ending {f['period_end']}: {fmt_usd(f['value_usd'])}")
    for (ticker, metric), points in series.items():
        lines.append(f"{ticker} {metric.replace('_', ' ')}: " + "; ".join(points))
    return [{"title": f"Zetaris USL: SEC 10-K financials for {target['entity_name']} and peers",
             "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={target['cik']}&type=10-K",
             "snippet": "", "text": "\n".join(lines), "dimension": "Financial health", "structured": True}]


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
with at most 6 queries, each including the company name. Audited SEC 10-K financials (revenue, net income, cash,
operating cash flow, long-term debt vs peers) are pulled from Zetaris automatically, so use web queries for
everything else and for very recent events."""

EXTRACT_PROMPT = f"""You are the Analyst's evidence extractor. From each numbered source, pull at most 3 factual
claims relevant to the question. Each claim must be backed by an exact short quote from that source's text.
Return {{"claims": [{{"source": "S1", "claim": "...", "quote": "...", "dimension": "<one of: {", ".join(DIMENSIONS)}>",
"signal": "positive|negative|neutral", "date": "date mentioned or empty"}}]}}.
Skip sources that are irrelevant or unreadable. Never invent facts."""

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
    plan = await ask_json(trace, "Planner", PLANNER_PROMPT, user, max_tokens=3000)
    plan["queries"] = plan.get("queries", [])[:6]
    trace.log("Planner", "plan", to="Retriever", hypotheses=plan.get("hypotheses", []), queries=plan["queries"])
    return plan


async def retriever(trace, company, queries, known_urls):
    first_round = not known_urls
    structured, results = await asyncio.gather(
        zetaris_financials(trace, company) if first_round else asyncio.sleep(0, []),
        asyncio.gather(*(web_search(q["query"]) for q in queries)))
    sources = list(structured)
    known_urls.update(s["url"] for s in structured)
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
    """One model call for all new sources (keeps us inside free-tier request quotas)."""
    if not sources:
        return []
    ids = {s["id"] for s in sources}
    blocks = "\n\n".join(f"[{s['id']}] {s['title']} ({s['url']})\n{s['text'] if s.get('structured') else s['text'][:SOURCE_CHARS]}" for s in sources)
    try:
        out = await ask_json(trace, "Analyst", EXTRACT_PROMPT, f"{task}\n\nSources:\n{blocks}", max_tokens=8000)
    except Exception as exc:
        trace.log("Analyst", "extract_failed", message=str(exc)[:200])
        return []
    return [c for c in out.get("claims", []) if c.get("claim") and c.get("source") in ids]


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
    memo = await ask_json(trace, "Analyst", ANALYST_PROMPT, user, max_tokens=5000)
    trace.log("Analyst", "draft", to="Critic", verdict=memo.get("verdict"), confidence=memo.get("confidence"))
    return memo


def uncovered_dimensions(evidence, memo) -> list[str]:
    """Dimensions that no reason, risk or contradiction in the memo cites evidence for."""
    by_id = {e["id"]: e for e in evidence}
    cited = {by_id[eid].get("dimension") for part in ("reasons", "risks", "contradictions")
             for item in memo.get(part, []) for eid in item.get("evidence", []) if eid in by_id}
    return [d for d in DIMENSIONS if d not in cited]


async def critic(trace, task, evidence, memo):
    uncovered = uncovered_dimensions(evidence, memo)
    trace.log("Critic", "coverage_check", uncovered=uncovered)
    try:
        review = await ask_json(trace, "Critic", CRITIC_PROMPT,
                                f"{task}\n\nEvidence:\n{evidence_lines(evidence)}\n\nDraft memo:\n"
                                f"{json.dumps(memo, ensure_ascii=False)}\n\nAutomated coverage check: the memo cites "
                                f"no evidence for these dimensions: {uncovered or 'none'}", max_tokens=3000)
    except ValueError as exc:
        # An unreadable review must not throw away the whole investigation.
        trace.log("Critic", "review_failed", message=str(exc)[:200])
        review = {"approved": False, "comment": "Critic review could not be parsed; draft not audited."}
    # Policy: a memo with a whole dimension unevidenced is not decision-ready, whatever the model says.
    if uncovered:
        review["missing_dimensions"] = sorted(set(review.get("missing_dimensions") or []) | set(uncovered))
        if review.get("approved"):
            review["approved"] = False
            review["comment"] = (f"Rejected by coverage policy: no cited evidence for {', '.join(uncovered)}. "
                                 + review.get("comment", ""))
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
        new_sources = await retriever(trace, company, plan["queries"], known_urls)
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
    result = {
        "run_id": trace.run_id,
        "memo": memo,
        "critic": review,
        "evidence": evidence,
        "sources": [{k: s[k] for k in ("id", "title", "url", "dimension")} for s in sources],
    }
    # The trace file ends with the result, so a finished trace doubles as a replayable sample.
    with trace.path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "result", **result}, ensure_ascii=False) + "\n")
    return result


async def replay_sample(company: str):
    """Sample mode: replay a recorded real run (trace events, then the result) without any API keys."""
    samples = sorted(SAMPLE_DIR.glob("*.jsonl"))
    if not samples:
        yield {"type": "error", "message": "No recorded samples in samples/"}
        return
    path = next((p for p in samples if company.lower().split()[0] in p.name.lower()), samples[0])
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for event in events:
        if event["type"] == "trace":
            await asyncio.sleep(0.6 if event["action"] in ("model_call", "tool_call") else 0.25)
            event = dict(event, replay=path.name)
        yield event


async def investigate(company: str, question: str, sample: bool = False):
    """Run the investigation, yielding trace events and finally the result (for streaming UIs)."""
    if sample or SAMPLE_MODE:
        async for event in replay_sample(company):
            yield event
        return
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
