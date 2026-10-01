"""HTTP API: health, jurisdictions, and the agentic chat endpoint."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import run_agent
from .auth import AuthUser, authenticate_user, create_access_token, current_user, require_privilege, ROLE_PRIVILEGES
from .config import get_settings
from .vectorstore import list_jurisdictions, list_documents, get_collection

logger = logging.getLogger("api")
router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)
    jurisdiction: str | None = None


class Citation(BaseModel):
    file_name: str | None = None
    jurisdiction: str | None = None
    page_number: int | None = None
    source_path: str | None = None
    score: float | None = None
    snippet: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    steps: int


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class IngestRequest(BaseModel):
    reset: bool = False
    skip_index: bool = False


@router.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "azure_configured": s.azure_configured}


@router.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    user = authenticate_user(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return AuthResponse(
        access_token=create_access_token(user.username, user.role),
        user={
            "username": user.username,
            "role": user.role,
            "privileges": user.privileges,
        },
    )


@router.get("/auth/me")
def me(user: AuthUser = Depends(current_user)) -> dict:
    return {"username": user.username, "role": user.role, "privileges": user.privileges}


@router.get("/auth/rbac")
def rbac(_: AuthUser = Depends(require_privilege("rbac:read"))) -> dict:
    return {"roles": ROLE_PRIVILEGES}


@router.get("/jurisdictions")
def jurisdictions(_: AuthUser = Depends(require_privilege("jurisdictions:read"))) -> dict:
    try:
        return {"jurisdictions": list_jurisdictions()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to list jurisdictions")
        raise HTTPException(status_code=503, detail=f"Vector store unavailable: {exc}")


@router.get("/documents")
def documents(_: AuthUser = Depends(require_privilege("documents:read"))) -> dict:
    try:
        return {"documents": list_documents()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to list documents")
        raise HTTPException(status_code=503, detail=f"Vector store unavailable: {exc}")


@router.get("/documents/file")
def get_document_file(
    path: str = Query(..., description="Relative source path of the document"),
    _: AuthUser = Depends(require_privilege("documents:read")),
) -> FileResponse:
    """Serve a PDF document by its source_path. Validates path stays within docs_dir."""
    s = get_settings()
    docs_root = Path(s.docs_dir).resolve()
    # Security: resolve and confirm the path does not escape docs_dir
    requested = (docs_root / path).resolve()
    if not str(requested).startswith(str(docs_root) + "/") and requested != docs_root:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if requested.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are served")
    return FileResponse(str(requested), media_type="application/pdf", filename=requested.name)


@router.delete("/documents/file")
def delete_document_file(
    path: str = Query(..., description="Relative source path of the document"),
    _: AuthUser = Depends(require_privilege("documents:delete")),
) -> dict:
    """Delete a PDF and all matching ChromaDB chunks, including orphaned chunks."""
    s = get_settings()
    docs_root = Path(s.docs_dir).resolve()
    relative_path = PurePosixPath(path.replace("\\", "/"))
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {".", ".."} for part in relative_path.parts)
    ):
        raise HTTPException(status_code=400, detail="Invalid path")
    source_path = relative_path.as_posix()
    requested = docs_root.joinpath(*relative_path.parts).resolve()
    try:
        requested.relative_to(docs_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path") from None
    if relative_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files can be deleted")
    file_exists = requested.is_file()

    # Do not report success or remove the file if ChromaDB cannot be updated.
    try:
        col = get_collection()
        existing = col.get(where={"source_path": source_path}, include=[])
        ids = existing.get("ids") or []
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not find ChromaDB chunks for %s", source_path)
        raise HTTPException(
            status_code=503,
            detail=f"Could not access document embeddings: {exc}",
        ) from exc

    if not file_exists and not ids:
        raise HTTPException(status_code=404, detail="Document and embeddings not found")

    if ids:
        try:
            col.delete(ids=ids)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not remove ChromaDB chunks for %s", source_path)
            raise HTTPException(
                status_code=503,
                detail=f"Could not delete document embeddings: {exc}",
            ) from exc

    if file_exists:
        try:
            requested.unlink()
        except OSError as exc:
            logger.exception("Could not delete document file %s", source_path)
            raise HTTPException(
                status_code=500,
                detail=f"Embeddings were removed, but the document file could not be deleted: {exc}",
            ) from exc

    logger.info("Deleted document: %s (%d chunks removed)", source_path, len(ids))
    return {"deleted": source_path, "chunks_removed": len(ids)}


# Only letters, digits, spaces, hyphens and underscores — prevents path traversal.
_JURISDICTION_RE = re.compile(r"^[A-Za-z0-9 \-_]+$")


@router.post("/documents/upload", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    jurisdiction: str = Form(...),
    _: AuthUser = Depends(require_privilege("documents:upload")),
) -> dict:
    """Upload a PDF, save it under docs_dir/<jurisdiction>/, then queue embedding."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    jurisdiction = jurisdiction.strip().upper()
    if not jurisdiction or not _JURISDICTION_RE.match(jurisdiction):
        raise HTTPException(
            status_code=400,
            detail="Jurisdiction name may only contain letters, digits, spaces, hyphens and underscores",
        )

    s = get_settings()
    docs_root = Path(s.docs_dir).resolve()
    jur_dir = docs_root / jurisdiction
    jur_dir.mkdir(parents=True, exist_ok=True)

    # Strip any directory components from the original filename.
    safe_name = Path(file.filename).name
    dest = jur_dir / safe_name

    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        await file.close()

    source_path = f"{jurisdiction}/{safe_name}"
    logger.info("Uploaded %s (%d bytes)", source_path, dest.stat().st_size)

    from .tasks import reprocess_document  # noqa: PLC0415
    async_result = reprocess_document.delay(source_path=source_path)
    return {"task_id": async_result.id, "source_path": source_path, "status": "queued"}


class ReprocessRequest(BaseModel):
    source_path: str


@router.post("/documents/reprocess", status_code=202)
def reprocess_document_endpoint(
    req: ReprocessRequest,
    _: AuthUser = Depends(require_privilege("documents:reprocess")),
) -> dict:
    """Enqueue a single-document reprocess job (parse -> clean -> chunk -> embed -> upsert)."""
    from .tasks import reprocess_document
    async_result = reprocess_document.delay(source_path=req.source_path)
    return {"task_id": async_result.id, "status": "queued"}


@router.get("/documents/reprocess/{task_id}")
def reprocess_status(
    task_id: str,
    _: AuthUser = Depends(require_privilege("documents:reprocess")),
) -> dict:
    """Poll the status of a reprocess job (reuses the same Celery result backend as ingest)."""
    from .celery_app import celery_app
    res = celery_app.AsyncResult(task_id)
    payload: dict = {"task_id": task_id, "state": res.state}
    if res.state == "PROGRESS":
        payload["progress"] = res.info
    elif res.state == "SUCCESS":
        payload["result"] = res.result
    elif res.state == "FAILURE":
        payload["error"] = str(res.info)
    return payload


@router.post("/ingest", status_code=202)
def trigger_ingest(
    req: IngestRequest,
    _: AuthUser = Depends(require_privilege("ingest:run")),
) -> dict:
    """Enqueue the corpus ingestion pipeline as a background Celery job."""
    from .tasks import run_ingestion

    async_result = run_ingestion.delay(reset=req.reset, skip_index=req.skip_index)
    return {"task_id": async_result.id, "status": "queued"}


@router.get("/ingest/{task_id}")
def ingest_status(
    task_id: str,
    _: AuthUser = Depends(require_privilege("ingest:run")),
) -> dict:
    """Poll the status/result of an ingestion job."""
    from .celery_app import celery_app

    res = celery_app.AsyncResult(task_id)
    payload: dict = {"task_id": task_id, "state": res.state}
    if res.state == "PROGRESS":
        payload["progress"] = res.info
    elif res.state == "SUCCESS":
        payload["result"] = res.result
    elif res.state == "FAILURE":
        payload["error"] = str(res.info)
    return payload


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    req: ChatRequest,
    _: AuthUser = Depends(require_privilege("chat:use")),
) -> ChatResponse:
    s = get_settings()
    if not s.azure_configured:
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI is not configured on the server.",
        )
    try:
        result = run_agent(
            question=req.question,
            history=[m.model_dump() for m in req.history],
            jurisdiction=req.jurisdiction,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return ChatResponse(**result)
