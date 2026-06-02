"""Admiral as an OpenAI-compatible service — the real replacement for stub_admiral.

Odysseus (the HUB front door) talks to this exactly as it would any model:
    GET  /v1/models           -> the "admiral" model
    POST /v1/chat/completions -> streaming or non-streaming OpenAI shape

The reply text + dispatch side-effects come from chad_admiral.admiral.reply(),
which runs real discovery, freezes CaptainDossiers into omni-mem, and spawns
captains as auto_runtime tracks.

Run:  uv run --with fastapi --with uvicorn --with pydantic \
          python -m chad_admiral.server      # 0.0.0.0:8901
"""
from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .admiral import reply, reply_stream

app = FastAPI(title="chad-admiral")
MODEL_ID = "admiral"


def _now() -> int:
    return int(time.time())


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "chad-fleet"}]}


def _chunk(content, finish=None):
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": _now(),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": ({} if content is None else {"content": content}),
                     "finish_reason": finish}],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))

    if not stream:
        text = reply(messages)
        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": _now(),
            "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def gen():
        first = _chunk(None)
        first["choices"][0]["delta"] = {"role": "assistant"}
        yield f"data: {json.dumps(first)}\n\n"
        # reply_stream yields text fragments; emit each as a delta so dispatch
        # progress (captain heartbeats, results) streams live to Odysseus.
        for fragment in reply_stream(messages):
            yield f"data: {json.dumps(_chunk(fragment))}\n\n"
        yield f"data: {json.dumps(_chunk(None, finish='stop'))}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8901)
