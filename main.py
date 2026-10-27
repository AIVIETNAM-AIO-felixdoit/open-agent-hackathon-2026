import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent import Trace, investigate, run_investigation

app = FastAPI(title="Due Diligence Agent")
STATIC = Path(__file__).parent / "static"


class Query(BaseModel):
    company: str
    question: str = ""
    sample: bool = False  # replay a recorded run instead of calling the model


@app.post("/run")
async def run(q: Query):
    """Blocking run: returns the final memo, evidence and trace id as JSON."""
    if q.sample:
        return [e async for e in investigate(q.company, q.question, sample=True)][-1]
    return await run_investigation(Trace(), q.company, q.question)


@app.post("/api/investigate")
async def stream(q: Query):
    """Streaming run used by the web UI (server-sent events)."""
    async def events():
        async for event in investigate(q.company, q.question, sample=q.sample):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
