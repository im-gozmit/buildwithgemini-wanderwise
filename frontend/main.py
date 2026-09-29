"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over the A2A protocol, returning replies as
structured parts the chat UI knows how to show:

  * {"kind": "text", "text": ...}  -> a normal chat bubble
  * {"kind": "a2ui", "data": ...}  -> one A2UI message (beginRendering /
    surfaceUpdate); static/index.html renders these as a card.

Why A2A: agents-cli 1.1.0 (GA) deploys ADK agents to Agent Runtime as A2A agents
and no longer registers the reasoning-engine operation schema the old
`agent_engines.get(...).stream_query()` path relied on (operation_schemas() comes
back empty). The container serves the A2A protocol over the Agent Engine HTTP
passthrough, so this proxy fetches the agent's card and sends messages with the
a2a-sdk client (the same path `agents-cli run --mode a2a` uses). This works for
both A2A and plain ADK 1.1.0 deployments (the container serves A2A either way).

Run:
  pip install -r requirements.txt
  export AGENT_ENGINE_RESOURCE_NAME="projects/.../locations/.../reasoningEngines/..."
  export AGENT_DIRECTORY="app"   # your agent's app directory (agents-cli-manifest.yaml)
  python main.py                 # -> http://localhost:8080
"""

import asyncio
import json
import logging
import os
import re
import uuid

import google.auth
import google.auth.transport.requests
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    FilePart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TextPart,
    TransportProtocol,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

RESOURCE = os.environ["AGENT_ENGINE_RESOURCE_NAME"]
# The agent's app directory (matches agent_directory in agents-cli-manifest.yaml).
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
# Location is embedded in the resource name: projects/<p>/locations/<loc>/reasoningEngines/<id>.
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

# A2A endpoint for an Agent Runtime deployment, via the Agent Engine HTTP
# passthrough. The card lives at the well-known path under this base.
A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"

# The agent tags its A2UI data parts with this mime type.
_A2UI_MIME = "application/json+a2ui"

# Cache ADC token and refresh only when expired (access tokens typically last 1 hr).
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

def _auth_headers() -> dict[str, str]:
    if not _creds.valid:
        _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


app = FastAPI()


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    # Always return JSON so the browser never receives a plain-text 500 page
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Reuse ONE A2A context per user so the agent remembers the conversation.
_contexts: dict[str, str] = {}
# Cache the agent card after the first fetch.
_card: AgentCard | None = None


async def _get_card(client: httpx.AsyncClient) -> AgentCard:
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        card = AgentCard(**resp.json())
        card.url = A2A_BASE
        _card = card
    return _card


def _extract_parts(parts: list) -> list[dict]:
    """Turn A2A response parts into structured parts for the chat UI.

    Text parts pass through as {"kind": "text"}. A2UI data parts (tagged
    application/json+a2ui) become {"kind": "a2ui", "data": <message>} so the UI
    renders the card; each data part is one A2UI message (beginRendering or
    surfaceUpdate).
    """
    out: list[dict] = []
    for p in parts:
        root = getattr(p, "root", p)
        if isinstance(root, TextPart) and getattr(root, "text", None):
            text = root.text
            # Check for <a2ui-json>...</a2ui-json> block
            match = re.search(r"<a2ui-json>([\s\S]*?)</a2ui-json>", text)
            if match:
                raw_json = match.group(1).strip()
                prose = (text[:match.start()] + text[match.end():]).strip()
                try:
                    payload = json.loads(raw_json)
                    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "component" in payload[0]:
                        root_id = payload[0].get("id", "root")
                        out.append({
                            "kind": "a2ui",
                            "data": {
                                "beginRendering": {"surfaceId": "1", "root": root_id},
                                "surfaceUpdate": {"surfaceId": "1", "components": payload},
                            },
                        })
                    elif isinstance(payload, list):
                        for item in payload:
                            out.append({"kind": "a2ui", "data": item})
                    elif isinstance(payload, dict):
                        out.append({"kind": "a2ui", "data": payload})
                except Exception as e:
                    logging.warning("Could not parse <a2ui-json> payload: %s", e)
                if prose:
                    out.append({"kind": "text", "text": prose})
            else:
                out.append({"kind": "text", "text": text})
        elif getattr(root, "data", None) is not None:
            data_val = root.data
            meta = getattr(root, "metadata", None) or {}
            # Handle cases where A2A wraps data as {'data': ..., 'metadata': {'mimeType': ...}}
            if isinstance(data_val, dict) and "data" in data_val and isinstance(data_val.get("metadata"), dict):
                meta = data_val.get("metadata") or {}
                data_val = data_val.get("data")
            mime = meta.get("mimeType") if isinstance(meta, dict) else None
            if mime == _A2UI_MIME:
                out.append({"kind": "a2ui", "data": data_val})
        elif isinstance(root, FilePart):
            uri = getattr(getattr(root, "file", None), "uri", None)
            if uri:
                out.append({"kind": "text", "text": uri})
    return out


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    user_id = body.get("user_id") or "web-user"
    accept_header = req.headers.get("accept", "")

    stream_requested = "text/event-stream" in accept_header or body.get("stream", True)

    async def event_generator():
        yield f"data: {json.dumps({'kind': 'status', 'text': 'Connecting with Ghumo Duniya…'})}\n\n"
        parts_emitted = 0
        last_task = None
        got_artifact_update = False

        queue = asyncio.Queue()

        async def worker():
            nonlocal last_task, got_artifact_update, parts_emitted
            try:
                async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
                    card = await _get_card(client)
                    factory = ClientFactory(
                        ClientConfig(
                            supported_transports=[
                                TransportProtocol.jsonrpc,
                                TransportProtocol.http_json,
                            ],
                            httpx_client=client,
                        )
                    )
                    a2a_client = factory.create(card)

                    msg = Message(
                        message_id=str(uuid.uuid4()),
                        role=Role.user,
                        parts=[Part(root=TextPart(text=message))],
                        context_id=_contexts.get(user_id),
                    )

                    async for event in a2a_client.send_message(msg):
                        if not isinstance(event, tuple):
                            continue
                        task, update = event
                        if task is not None:
                            last_task = task
                            if getattr(task, "context_id", None):
                                _contexts[user_id] = task.context_id

                        if isinstance(update, TaskArtifactUpdateEvent):
                            got_artifact_update = True
                            extracted = _extract_parts(update.artifact.parts)
                            for p in extracted:
                                parts_emitted += 1
                                await queue.put({"kind": "part", "part": p})

                    if not got_artifact_update and last_task is not None:
                        for artifact in getattr(last_task, "artifacts", None) or []:
                            for p in _extract_parts(artifact.parts):
                                parts_emitted += 1
                                await queue.put({"kind": "part", "part": p})
            except Exception as e:
                logger.error("A2A worker stream error: %s", e)
                await queue.put({"kind": "part", "part": {"kind": "text", "text": f"Error communicating with agent: {e}"}})
            finally:
                await queue.put({"kind": "done"})

        asyncio.create_task(worker())

        # Dynamic thought progress ticker
        thoughts = [
            "Ghumo Duniya is exploring routes, spots & options…",
            "Checking local details & calculating prices in ₹…",
            "Assembling your custom travel card…",
        ]
        thought_idx = 0

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.1)
                if item["kind"] == "done":
                    break
                yield f"data: {json.dumps(item)}\n\n"
            except asyncio.TimeoutError:
                if thought_idx < len(thoughts):
                    yield f"data: {json.dumps({'kind': 'status', 'text': thoughts[thought_idx]})}\n\n"
                    thought_idx += 1
                else:
                    yield f"data: {json.dumps({'kind': 'status', 'text': 'Finalizing your recommendations…'})}\n\n"

        if parts_emitted == 0:
            yield f"data: {json.dumps({'kind': 'part', 'part': {'kind': 'text', 'text': '(Ghumo Duniya finished processing without returning a reply.)'}})}\n\n"

        yield f"data: {json.dumps({'kind': 'done'})}\n\n"

    if stream_requested:
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # Non-streaming fallback for simple REST clients
    all_parts = []
    async for line in event_generator():
        if line.startswith("data: "):
            ev = json.loads(line[6:].strip())
            if ev.get("kind") == "part":
                all_parts.append(ev.get("part"))
    return JSONResponse({"parts": all_parts})


# Serve the chat UI (keep this mount last so /chat wins).
from pathlib import Path

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
