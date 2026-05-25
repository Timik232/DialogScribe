import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, cast

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.audio_processor import AudioProcessor
from gigaam_transcriber.data_models import TranscriptionResult
from gigaam_transcriber.auth import bootstrap_admin, get_current_user
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.models import User
from gigaam_transcriber.mindmap import get_stored_mindmap

from routers._helpers import (
    API_KEY,
    SUPPORTED_EXTENSIONS,
    _handle_transcription_exception,
    _map_format,
    _openai_error,
    _result_response,
    _verify_auth,
)

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "1024"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "7860"))

logger = logging.getLogger("dialogscribe-api")

# SvelteKit SPA build directory
_BUILD_DIR = Path(__file__).parent / "frontend" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI):
    transcriber = GigaAMTranscriber()
    app.state.transcriber = transcriber
    logger.info("DialogScribe API starting — transcriber initialized")

    # Bootstrap admin user from ADMIN_EMAIL / ADMIN_PASSWORD env vars
    async with async_session_factory() as db:
        try:
            await bootstrap_admin(db)
        except Exception:
            logger.exception("Failed to bootstrap admin user")

    yield
    transcriber.cleanup()
    logger.info("DialogScribe API shutting down — transcriber cleaned up")


app = FastAPI(title="DialogScribe API", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "transcriber": "ready"}


@app.post("/v1/audio/transcriptions")
def create_transcription(
    file: Annotated[UploadFile, File()],
    auth: Annotated[None, Depends(_verify_auth)],
    model: Annotated[str, Form()] = "whisper-1",
    language: Annotated[str | None, Form()] = None,
    response_format: Annotated[str, Form()] = "json",
) -> Response:
    _ = model, auth
    filename = file.filename or ""
    file_ext = Path(filename).suffix.lower()

    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=_openai_error(
                f"Unsupported file format: '{file_ext or 'unknown'}'",
                "invalid_request_error",
                400,
            ),
        )

    max_size_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
            tmp_path = tmp_file.name
            size = 0
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=_openai_error(
                            f"File too large. Maximum allowed is {MAX_UPLOAD_SIZE_MB}MB",
                            "invalid_request_error",
                            413,
                        ),
                    )
                _ = tmp_file.write(chunk)

        transcriber = cast(GigaAMTranscriber, app.state.transcriber)
        result: TranscriptionResult = transcriber.transcribe(
            input_path=tmp_path,
            diarization="none",
            language=language or "ru",
            output_format=_map_format(response_format),
        )
        return _result_response(result, response_format)
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_transcription_exception(e)
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            logger.warning("Failed to remove temp file: %s", tmp_path)


from routers import admin, analysis, autoflow, exports, live_hints, meeting_prep, saved_transcriptions, templates, transcription, usage  # noqa: E402
from routers.auth import auth_router  # noqa: E402

app.include_router(auth_router)
app.include_router(transcription.router)
app.include_router(analysis.router)
app.include_router(templates.router)
app.include_router(exports.router)
app.include_router(autoflow.router)
app.include_router(usage.router)
app.include_router(admin.router)
app.include_router(saved_transcriptions.router)
app.include_router(live_hints.router)
app.include_router(meeting_prep.router)


@app.get("/mindmap/{uid}")
async def serve_mindmap(uid: str):
    """Serve stored mindmap HTML. Returns 404 if UID not found."""
    html_content = get_stored_mindmap(uid)
    if html_content is None:
        return Response(status_code=404, content='{"detail":"Not Found"}', media_type="application/json")
    return HTMLResponse(content=html_content)


# Serve mindmap JS libraries locally (avoids CDN dependency)
_MINDMAP_STATIC_DIR = Path(__file__).parent / "gigaam_transcriber" / "static"
if _MINDMAP_STATIC_DIR.is_dir():
    app.mount("/mindmap-static", StaticFiles(directory=str(_MINDMAP_STATIC_DIR)), name="mindmap-static")


# Serve SvelteKit SPA — catch-all route for client-side routing
_SPA_INDEX = _BUILD_DIR / "index.html"

if _BUILD_DIR.is_dir():

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        """Serve SPA static assets or fall back to index.html for client-side routing."""
        file_path = _BUILD_DIR / path
        if path and file_path.is_file():
            return FileResponse(file_path)
        return HTMLResponse(content=_SPA_INDEX.read_text())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=HOST, port=PORT, timeout_keep_alive=300)
