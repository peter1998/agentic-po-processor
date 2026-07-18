"""FastAPI entry point: two endpoints.

POST /webhook/email — accepts a file, returns 202 immediately, runs the
LangGraph pipeline in the background.

POST /review/approve — moves a flagged order into the approved table,
protected by an API key header.
"""

import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, UploadFile
from pydantic import BaseModel

from agents.graph import build_graph
from models.schema import GraphState
from utils.config import settings
import services.storage as storage
import services.vectorstore as vectorstore

app = FastAPI(title="Agentic PO Processor")

_graph_app = build_graph()

_EXTENSION_TO_FILE_TYPE = {
    ".csv": "csv",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
}

_UPLOAD_DIR = Path("data/uploads")


@app.on_event("startup")
def on_startup() -> None:
    storage.init_db()
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    vectorstore.build_corpus()


def _verify_api_key(x_api_key: str = Header(...)) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _run_pipeline(file_path: str, file_type: str, correlation_id: str) -> None:
    """Runs in the background, after the 202 response has already been sent."""
    state = GraphState(file_path=file_path, file_type=file_type, correlation_id=correlation_id)
    _graph_app.invoke(state)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook/email", status_code=202)
async def webhook_email(file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    extension = Path(file.filename).suffix.lower()
    file_type = _EXTENSION_TO_FILE_TYPE.get(extension)
    if file_type is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{extension}'. Supported: {list(_EXTENSION_TO_FILE_TYPE.keys())}",
        )

    correlation_id = str(uuid.uuid4())
    saved_path = _UPLOAD_DIR / f"{correlation_id}{extension}"
    contents = await file.read()
    saved_path.write_bytes(contents)

    background_tasks.add_task(_run_pipeline, str(saved_path), file_type, correlation_id)

    return {"status": "accepted", "correlation_id": correlation_id}


class ApproveRequest(BaseModel):
    review_id: int


@app.post("/review/approve")
def review_approve(request: ApproveRequest, x_api_key: str = Header(...)) -> dict:
    _verify_api_key(x_api_key)
    try:
        new_order_id = storage.approve_pending_review(request.review_id)
    except ValueError as e:
        message = str(e)
        status_code = 404 if "No pending review" in message else 409
        raise HTTPException(status_code=status_code, detail=message)

    return {"status": "approved", "purchase_order_id": new_order_id}