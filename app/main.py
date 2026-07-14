import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlite3 import Connection

from app.admin_store import set_admin_password, verify_admin_credentials
from app.artifact_store import (
    ArtifactConflict,
    ArtifactNotFound,
    artifact_upload_status,
    complete_artifact_upload,
    create_artifact_record,
    get_artifact,
    get_uploaded_artifact_path,
    list_task_artifacts,
    store_artifact_chunk,
    store_artifact_content,
)
from app.config import get_settings
from app.dashboard import dashboard_html
from app.database import get_connection, init_db
from app.dataset_store import (
    DatasetConflict,
    DatasetNotFound,
    delete_dataset_version,
    get_dataset_version,
    is_path_within_roots,
    list_dataset_versions,
    list_worker_caches_for_worker,
    list_worker_caches,
    pending_delete_requests,
    register_dataset_version,
    request_worker_cache_delete,
    upsert_worker_cache,
)
from app.installer_store import (
    WorkerInstallInviteError,
    WorkerInstallInviteExpired,
    WorkerInstallInviteNotFound,
    WorkerInstallInviteUsed,
    create_worker_install_invite,
    get_worker_install_invite,
    mark_worker_install_invite_used,
)
from app.task_store import (
    PayloadTooLarge,
    QueueLimitExceeded,
    ResourceUnsatisfiable,
    TaskConflict,
    TaskNotFound,
    WorkerNotRegistered,
    assert_task_owned_by_worker,
    cancel_task,
    claim_task,
    create_task,
    get_task_for_submitter,
    get_worker,
    get_worker_secret_hash,
    hash_worker_secret,
    get_task,
    effective_worker_settings,
    list_task_summaries,
    list_workers,
    queue_status,
    register_worker,
    report_failed,
    report_success,
    task_summary,
    touch_worker,
    update_worker_concurrency,
    update_worker_settings,
    verify_worker_secret,
)
from app.worker_installer import (
    build_worker_package,
    render_posix_install_script,
    render_windows_install_script,
    worker_package_sha256,
    worker_env_text,
    worker_install_command,
)


app = FastAPI(title="Cloudlink Task Queue")
app.add_middleware(GZipMiddleware, minimum_size=1024)
init_db()
basic_auth = HTTPBasic()


class CreateTaskRequest(BaseModel):
    type: str
    payload: Dict[str, Any]
    title: str = ""
    description: str = ""
    submitter_id: Optional[str] = None
    group_id: Optional[str] = None


class CancelTaskRequest(BaseModel):
    reason: str = ""


class ClaimTaskRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    supported_types: List[str] = Field(default_factory=list)
    capacity_state: Optional[Dict[str, Any]] = None
    active_task_count: Optional[int] = None


class SuccessRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    result: Dict[str, Any]
    logs: Optional[str] = None


class FailedRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    error: str
    logs: Optional[str] = None
    error_code: Optional[str] = None


class RegisterWorkerRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    display_name: Optional[str] = None
    supported_types: List[str] = Field(default_factory=list)
    enabled: bool = True
    hardware_profile: Optional[Dict[str, Any]] = None
    runtime_profile: Optional[Dict[str, Any]] = None
    capacity_state: Optional[Dict[str, Any]] = None
    max_concurrent_tasks: int = 1
    active_task_count: int = 0


class HeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    supported_types: List[str] = Field(default_factory=list)
    last_error: Optional[str] = None
    hardware_profile: Optional[Dict[str, Any]] = None
    runtime_profile: Optional[Dict[str, Any]] = None
    capacity_state: Optional[Dict[str, Any]] = None
    dataset_root_checks: Optional[List[Dict[str, Any]]] = None
    max_concurrent_tasks: Optional[int] = None
    active_task_count: Optional[int] = None


class RegisterDatasetRequest(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: Optional[str] = None
    description: str = ""
    source_kind: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    content_type: Optional[str] = None
    archive_format: Optional[str] = None
    extract_required: bool = False
    manifest: Dict[str, Any] = Field(default_factory=dict)
    created_by: str = "internal"
    compute_sha256: bool = False


class WorkerCacheRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    local_archive_path: Optional[str] = None
    local_extracted_path: Optional[str] = None
    size_bytes: int = 0
    extracted_size_bytes: int = 0
    checksum_sha256: Optional[str] = None
    data_root_path: Optional[str] = None
    last_error: Optional[str] = None
    last_used_at: Optional[str] = None


class WorkerDeleteRequest(BaseModel):
    worker_id: Optional[str] = None


class WorkerConcurrencyRequest(BaseModel):
    max_concurrent_tasks: int = Field(ge=1)


class WorkerDatasetRootRequest(BaseModel):
    path: str = Field(min_length=1)
    mode: str = "active"
    label: Optional[str] = None


class WorkerReserveSettingsRequest(BaseModel):
    cpu_cores: Optional[int] = Field(default=None, ge=0)
    memory_bytes: Optional[int] = Field(default=None, ge=0)
    job_disk_bytes: Optional[int] = Field(default=None, ge=0)
    dataset_disk_bytes: Optional[int] = Field(default=None, ge=0)
    gpu_memory_bytes: Optional[int] = Field(default=None, ge=0)


class WorkerSettingsRequest(BaseModel):
    max_concurrent_tasks: int = Field(ge=1)
    job_root: Optional[str] = None
    dataset_roots: Optional[List[WorkerDatasetRootRequest]] = None
    reserve_overrides: Optional[WorkerReserveSettingsRequest] = None


class ChangeAdminPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=256)
    confirm_password: str = Field(min_length=8, max_length=256)


class CreateWorkerInstallInviteRequest(BaseModel):
    platform: str = Field(min_length=1)
    worker_id: Optional[str] = None
    display_name: Optional[str] = None


class WorkerInstallRegisterRequest(BaseModel):
    hostname: Optional[str] = None
    platform: Optional[str] = None


class CreateArtifactRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    meaning: str = ""
    content_type: Optional[str] = None
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=1)
    required: bool = True


@dataclass(frozen=True)
class AuthContext:
    kind: str
    submitter_id: Optional[str] = None


def require_worker_auth(authorization: Optional[str] = Header(default=None)) -> str:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid worker credentials")
    token = authorization[len(prefix) :]
    if not token:
        raise HTTPException(status_code=401, detail="Invalid worker credentials")
    return token


def require_internal_auth(
    request: Request,
    x_internal_api_secret: Optional[str] = Header(
        default=None,
        alias="X-Internal-API-Secret",
    )
) -> None:
    require_direct_local_request(request)
    expected = get_settings().internal_api_secret
    if not expected or not secrets.compare_digest(x_internal_api_secret or "", expected):
        raise HTTPException(status_code=401, detail="Invalid internal credentials")


def is_direct_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        return False
    request_host = (request.headers.get("host") or "").split(":", 1)[0].strip("[]").lower()
    if request_host and request_host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        return False
    forwarded_headers = {
        "forwarded",
        "x-forwarded-for",
        "x-real-ip",
        "cf-connecting-ip",
        "true-client-ip",
    }
    return not any(request.headers.get(header) for header in forwarded_headers)


def require_direct_local_request(request: Request) -> None:
    if not is_direct_local_request(request):
        raise HTTPException(status_code=403, detail="Internal API requires direct local access")


def require_internal_or_codex_auth(
    request: Request,
    x_internal_api_secret: Optional[str] = Header(
        default=None,
        alias="X-Internal-API-Secret",
    ),
    x_cloudlink_codex_token: Optional[str] = Header(
        default=None,
        alias="X-Cloudlink-Codex-Token",
    ),
) -> AuthContext:
    settings = get_settings()
    require_direct_local_request(request)
    if settings.internal_api_secret and secrets.compare_digest(
        x_internal_api_secret or "",
        settings.internal_api_secret,
    ):
        return AuthContext(kind="internal")
    token = x_cloudlink_codex_token or ""
    for submitter_id, expected_token in settings.codex_tokens.items():
        if expected_token and secrets.compare_digest(token, expected_token):
            return AuthContext(kind="codex", submitter_id=submitter_id)
    raise HTTPException(status_code=401, detail="Invalid internal credentials")


def submitter_filter_for_auth(auth: AuthContext) -> Optional[str]:
    return auth.submitter_id if auth.kind == "codex" else None


def require_admin_auth(
    credentials: HTTPBasicCredentials = Depends(basic_auth),
    conn: Connection = Depends(get_connection),
) -> None:
    settings = get_settings()
    if not verify_admin_credentials(
        conn,
        credentials.username,
        credentials.password,
        settings,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def map_store_error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, TaskNotFound):
        return HTTPException(status_code=404, detail="Task not found")
    if isinstance(error, ArtifactNotFound):
        return HTTPException(status_code=404, detail="Artifact not found")
    if isinstance(error, DatasetNotFound):
        return HTTPException(status_code=404, detail="Dataset not found")
    if isinstance(error, WorkerNotRegistered):
        return HTTPException(status_code=403, detail="Worker is not registered")
    if isinstance(error, (TaskConflict, ArtifactConflict, DatasetConflict)):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ResourceUnsatisfiable):
        return HTTPException(status_code=422, detail=error.detail)
    if isinstance(error, QueueLimitExceeded):
        return HTTPException(status_code=429, detail=error.detail)
    if isinstance(error, PayloadTooLarge):
        return HTTPException(status_code=413, detail=str(error))
    return HTTPException(status_code=500, detail="Internal server error")


def public_base_url(request: Request) -> str:
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url
    return str(request.base_url).rstrip("/")


def validate_worker_install_base_url(base_url: str) -> None:
    settings = get_settings()
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
        return
    if settings.allow_insecure_worker_install:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "Worker install public base URL must use HTTPS. Set "
            "CLOUDLINK_ALLOW_INSECURE_WORKER_INSTALL=1 only for explicit testing."
        ),
    )


def map_install_invite_error(error: Exception) -> HTTPException:
    if isinstance(error, WorkerInstallInviteNotFound):
        return HTTPException(status_code=404, detail="Worker install invite not found")
    if isinstance(error, (WorkerInstallInviteExpired, WorkerInstallInviteUsed)):
        return HTTPException(status_code=410, detail=str(error))
    if isinstance(error, WorkerInstallInviteError):
        return HTTPException(status_code=400, detail=str(error))
    return map_store_error(error)


def get_worker_for_api(
    conn: Connection,
    worker_id: str,
    worker_token: str,
) -> Dict[str, Any]:
    worker = get_worker(conn, worker_id)
    if not worker["enabled"]:
        raise WorkerNotRegistered(worker_id)
    secret_hash = get_worker_secret_hash(conn, worker_id)
    settings = get_settings()
    if secret_hash:
        if not verify_worker_secret(worker_token, secret_hash):
            raise HTTPException(status_code=401, detail="Invalid worker credentials")
    elif not settings.worker_secret or not secrets.compare_digest(
        worker_token,
        settings.worker_secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid worker credentials")
    return worker


def stable_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_section(value: Any) -> str:
    return hashlib.sha256(stable_json_bytes(value)).hexdigest()


def parse_section_etags(value: Optional[str]) -> Dict[str, str]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(key): str(section_value)
        for key, section_value in decoded.items()
        if isinstance(key, str) and isinstance(section_value, str)
    }


def lightweight_dataset_version(dataset: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in dataset.items()
        if key != "manifest"
    }


def dashboard_overview_sections(conn: Connection) -> Dict[str, Any]:
    return {
        "summary": task_summary(conn),
        "workers": list_workers(conn),
        "tasks": list_task_summaries(conn),
        "datasets": [
            lightweight_dataset_version(dataset)
            for dataset in list_dataset_versions(conn)
        ],
        "dataset_caches": list_worker_caches(conn),
    }


def section_etags(sections: Dict[str, Any]) -> Dict[str, str]:
    return {
        name: digest_section(value)
        for name, value in sections.items()
    }


def overview_etag(etags: Dict[str, str]) -> str:
    return f'W/"{digest_section(etags)}"'


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def dashboard() -> str:
    return dashboard_html()


@app.get("/api/admin/overview", dependencies=[Depends(require_admin_auth)])
def api_admin_overview(
    request: Request,
    response: Response,
    conn: Connection = Depends(get_connection),
) -> Any:
    sections = dashboard_overview_sections(conn)
    current_section_etags = section_etags(sections)
    current_overview_etag = overview_etag(current_section_etags)
    response_headers = {
        "ETag": current_overview_etag,
        "X-Cloudlink-Section-Etags": json.dumps(
            current_section_etags,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "Cache-Control": "no-store",
    }
    if request.headers.get("if-none-match") == current_overview_etag:
        return Response(status_code=304, headers=response_headers)

    previous_section_etags = parse_section_etags(
        request.headers.get("x-cloudlink-section-etags")
    )
    changed_sections = [
        name
        for name, etag in current_section_etags.items()
        if previous_section_etags.get(name) != etag
    ]
    response.headers.update(response_headers)
    body: Dict[str, Any] = {
        "changed_sections": changed_sections,
        "section_etags": current_section_etags,
    }
    for name in changed_sections:
        body[name] = sections[name]
    return body


@app.get("/api/admin/tasks/{task_id}", dependencies=[Depends(require_admin_auth)])
def api_admin_get_task(
    task_id: str,
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        task = get_task(conn, task_id)
        task["artifacts"] = list_task_artifacts(conn, task_id)
        return task
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.post("/api/admin/password", dependencies=[Depends(require_admin_auth)])
def api_admin_change_password(
    body: ChangeAdminPasswordRequest,
    credentials: HTTPBasicCredentials = Depends(basic_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, str]:
    settings = get_settings()
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Password confirmation does not match")
    if not verify_admin_credentials(
        conn,
        credentials.username,
        body.current_password,
        settings,
    ):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    set_admin_password(conn, settings.admin_username, body.new_password)
    return {"status": "ok"}


@app.post(
    "/api/admin/worker-install-invites",
    dependencies=[Depends(require_admin_auth)],
)
def api_admin_create_worker_install_invite(
    request: Request,
    body: CreateWorkerInstallInviteRequest,
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    settings = get_settings()
    base_url = public_base_url(request)
    validate_worker_install_base_url(base_url)
    package_sha256 = worker_package_sha256()
    try:
        invite = create_worker_install_invite(
            conn,
            platform=body.platform,
            worker_id=body.worker_id,
            display_name=body.display_name,
            public_base_url=base_url,
            ttl_minutes=settings.worker_install_invite_ttl_minutes,
        )
    except Exception as exc:
        raise map_install_invite_error(exc) from exc
    script_name = "install.ps1" if invite["platform"] == "windows" else "install.sh"
    script_url = f"{base_url}/install/worker/{invite['token']}/{script_name}"
    package_url = f"{base_url}/install/worker/{invite['token']}/package.tar.gz"
    return {
        "worker_id": invite["worker_id"],
        "display_name": invite["display_name"],
        "platform": invite["platform"],
        "expires_at": invite["expires_at"],
        "script_url": script_url,
        "package_url": package_url,
        "package_sha256": package_sha256,
        "command": worker_install_command(invite["platform"], script_url),
    }


@app.get("/install/worker/{token}/install.sh", response_class=PlainTextResponse)
def api_worker_install_shell_script(
    token: str,
    conn: Connection = Depends(get_connection),
) -> str:
    try:
        invite = get_worker_install_invite(conn, token)
        if invite["platform"] == "windows":
            raise WorkerInstallInviteNotFound("wrong platform")
        return render_posix_install_script(
            base_url=invite["public_base_url"],
            token=token,
            package_sha256=worker_package_sha256(),
        )
    except Exception as exc:
        raise map_install_invite_error(exc) from exc


@app.get("/install/worker/{token}/install.ps1", response_class=PlainTextResponse)
def api_worker_install_powershell_script(
    token: str,
    conn: Connection = Depends(get_connection),
) -> str:
    try:
        invite = get_worker_install_invite(conn, token)
        if invite["platform"] != "windows":
            raise WorkerInstallInviteNotFound("wrong platform")
        return render_windows_install_script(
            base_url=invite["public_base_url"],
            token=token,
            package_sha256=worker_package_sha256(),
        )
    except Exception as exc:
        raise map_install_invite_error(exc) from exc


@app.get("/install/worker/{token}/package.tar.gz")
def api_worker_install_package(
    token: str,
    conn: Connection = Depends(get_connection),
) -> Response:
    try:
        get_worker_install_invite(conn, token)
    except Exception as exc:
        raise map_install_invite_error(exc) from exc
    return Response(
        build_worker_package(),
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="cloudlink-worker.tar.gz"'},
    )


@app.post("/install/worker/{token}/register")
def api_worker_install_register(
    token: str,
    body: WorkerInstallRegisterRequest,
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        invite = get_worker_install_invite(conn, token)
        if body.platform and invite["platform"] == "windows" and body.platform != "windows":
            raise WorkerInstallInviteError("platform does not match invite")
        settings = get_settings()
        supported_types = sorted(settings.allowed_task_types)
        worker_secret = secrets.token_urlsafe(32)
        worker = register_worker(
            conn,
            worker_id=invite["worker_id"],
            display_name=invite["display_name"],
            supported_types=supported_types,
            install_platform=invite["platform"],
            enabled=True,
            worker_secret_hash=hash_worker_secret(worker_secret),
        )
        mark_worker_install_invite_used(conn, token)
    except Exception as exc:
        raise map_install_invite_error(exc) from exc
    return {
        "status": "ok",
        "worker_id": worker["worker_id"],
        "display_name": worker["display_name"],
        "api_base_url": invite["public_base_url"],
        "worker_secret": worker_secret,
        "env": worker_env_text(
            api_base_url=invite["public_base_url"],
            worker_secret=worker_secret,
            worker_id=worker["worker_id"],
            supported_types=supported_types,
        ),
    }


@app.get("/api/internal/status")
def api_internal_status(
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    submitter_id = submitter_filter_for_auth(auth)
    resources = queue_status(conn)
    return {
        "summary": task_summary(conn, submitter_id=submitter_id),
        "workers": list_workers(conn),
        "resource_status": resources,
    }


@app.get("/api/internal/queue/status")
def api_internal_queue_status(
    _auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    return queue_status(conn)


@app.post("/api/internal/workers", dependencies=[Depends(require_internal_auth)])
def api_register_worker(
    body: RegisterWorkerRequest,
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        return register_worker(
            conn,
            worker_id=body.worker_id,
            display_name=body.display_name or body.worker_id,
            supported_types=body.supported_types,
            enabled=body.enabled,
            hardware_profile=body.hardware_profile,
            runtime_profile=body.runtime_profile,
            capacity_state=body.capacity_state,
            max_concurrent_tasks=body.max_concurrent_tasks,
            active_task_count=body.active_task_count,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.post("/api/internal/tasks")
def api_create_task(
    body: CreateTaskRequest,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    settings = get_settings()
    if body.type not in settings.allowed_task_types:
        raise HTTPException(status_code=400, detail="Unsupported task type")
    try:
        task = create_task(
            conn,
            body.type,
            body.payload,
            body.title,
            body.description,
            submitter_id=(
                auth.submitter_id
                if auth.kind == "codex"
                else body.submitter_id
            ),
            group_id=body.group_id,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "title": task["title"],
        "description": task["description"],
        "submitter_id": task["submitter_id"],
        "group_id": task["group_id"],
        "created_at": task["created_at"],
        "resource_status": queue_status(conn),
    }


@app.get("/api/internal/tasks")
def api_list_internal_tasks(
    limit: int = 100,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    submitter_id = submitter_filter_for_auth(auth)
    resources = queue_status(conn)
    return {
        "tasks": list_task_summaries(
            conn,
            limit=limit,
            submitter_id=submitter_id,
        ),
        "resource_status": resources,
    }


@app.get("/api/internal/tasks/{task_id}")
def api_get_task(
    task_id: str,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        queue_status(conn)
        return get_task_for_submitter(
            conn,
            task_id,
            submitter_filter_for_auth(auth),
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.post("/api/internal/tasks/{task_id}/cancel")
def api_cancel_task(
    task_id: str,
    body: CancelTaskRequest,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        return cancel_task(
            conn,
            task_id,
            submitter_filter_for_auth(auth),
            reason=body.reason,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.get(
    "/api/internal/tasks/{task_id}/artifacts",
)
def api_internal_list_task_artifacts(
    task_id: str,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_task_for_submitter(
            conn,
            task_id,
            submitter_filter_for_auth(auth),
        )
        return {"artifacts": list_task_artifacts(conn, task_id)}
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.get(
    "/api/internal/tasks/{task_id}/artifacts/{artifact_id}/download",
)
def api_internal_download_task_artifact(
    task_id: str,
    artifact_id: str,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> FileResponse:
    try:
        get_task_for_submitter(
            conn,
            task_id,
            submitter_filter_for_auth(auth),
        )
        artifact = get_artifact(conn, artifact_id)
        if artifact["task_id"] != task_id:
            raise ArtifactNotFound(artifact_id)
        path = get_uploaded_artifact_path(conn, artifact_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    return FileResponse(
        path,
        media_type=artifact.get("content_type") or "application/octet-stream",
        filename=artifact["display_name"],
    )


@app.post("/api/internal/datasets")
def api_register_dataset(
    body: RegisterDatasetRequest,
    auth: AuthContext = Depends(require_internal_or_codex_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    settings = get_settings()
    source_kind = body.source_kind
    copy_source = False
    if auth.kind == "codex":
        if is_path_within_roots(body.source_path, settings.allowed_dataset_source_roots):
            pass
        elif is_path_within_roots(body.source_path, settings.codex_dataset_source_roots):
            copy_source = True
            if source_kind == "symlink_file":
                source_kind = "owned_file"
        else:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Dataset source path is outside allowed dataset source roots "
                    "and Codex dataset source roots"
                ),
            )
    try:
        return register_dataset_version(
            conn,
            name=body.name,
            version=body.version,
            title=body.title or body.name,
            description=body.description,
            source_kind=source_kind,
            source_path=body.source_path,
            content_type=body.content_type,
            archive_format=body.archive_format,
            extract_required=body.extract_required,
            manifest_extra=body.manifest,
            created_by=body.created_by,
            compute_sha256=body.compute_sha256,
            copy_source=copy_source,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.get("/api/internal/datasets", dependencies=[Depends(require_internal_or_codex_auth)])
def api_list_datasets(conn: Connection = Depends(get_connection)) -> Dict[str, Any]:
    return {"datasets": list_dataset_versions(conn)}


@app.delete(
    "/api/internal/datasets/{dataset_version_id}",
    dependencies=[Depends(require_internal_auth)],
)
def api_delete_dataset_internal(
    dataset_version_id: str,
    conn: Connection = Depends(get_connection),
) -> Dict[str, str]:
    try:
        delete_dataset_version(conn, dataset_version_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {"status": "ok"}


@app.post("/api/worker/claim", dependencies=[Depends(require_worker_auth)])
def api_claim_task(
    body: ClaimTaskRequest,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, body.worker_id, worker_token)
        task = claim_task(
            conn,
            body.worker_id,
            body.supported_types,
            capacity_state=body.capacity_state,
            active_task_count=body.active_task_count,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {"task": task}


@app.post("/api/worker/heartbeat", dependencies=[Depends(require_worker_auth)])
def api_worker_heartbeat(
    body: HeartbeatRequest,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, body.worker_id, worker_token)
        worker = touch_worker(
            conn,
            body.worker_id,
            body.supported_types,
            body.last_error,
            hardware_profile=body.hardware_profile,
            runtime_profile=body.runtime_profile,
            capacity_state=body.capacity_state,
            dataset_root_checks=body.dataset_root_checks,
            max_concurrent_tasks=None,
            active_task_count=body.active_task_count,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {
        "status": "ok",
        "required_version": worker["required_version"],
        "minimum_worker_version": worker["minimum_worker_version"],
        "server_version": worker["server_version"],
        "worker_version": worker["worker_version"],
        "version_status": worker["version_status"],
        "needs_update": worker["needs_update"],
        "max_concurrent_tasks": worker["max_concurrent_tasks"],
        "settings": effective_worker_settings(worker),
    }


@app.get(
    "/api/worker/datasets/caches",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_dataset_caches(
    worker_id: str,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, worker_id, worker_token)
        return {"caches": list_worker_caches_for_worker(conn, worker_id)}
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.get(
    "/api/worker/datasets/delete-requests",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_dataset_delete_requests(
    worker_id: str,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, worker_id, worker_token)
        return {"requests": pending_delete_requests(conn, worker_id)}
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.get(
    "/api/worker/datasets/{dataset_version_id}",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_dataset_metadata(
    dataset_version_id: str,
    worker_id: str,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, worker_id, worker_token)
        version = get_dataset_version(conn, dataset_version_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    filename = Path(version["server_path"]).name
    return {
        **version,
        "filename": filename,
        "download_url": (
            f"/api/worker/datasets/{dataset_version_id}/download"
            f"?worker_id={worker_id}"
        ),
    }


@app.get(
    "/api/worker/datasets/{dataset_version_id}/download",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_dataset_download(
    dataset_version_id: str,
    worker_id: str,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> FileResponse:
    try:
        get_worker_for_api(conn, worker_id, worker_token)
        version = get_dataset_version(conn, dataset_version_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    path = Path(version["server_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Dataset file not found")
    return FileResponse(
        path,
        media_type=version.get("content_type") or "application/octet-stream",
        filename=path.name,
    )


@app.post(
    "/api/worker/datasets/{dataset_version_id}/cache",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_dataset_cache(
    dataset_version_id: str,
    body: WorkerCacheRequest,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, body.worker_id, worker_token)
        return upsert_worker_cache(
            conn,
            worker_id=body.worker_id,
            dataset_version_id=dataset_version_id,
            status=body.status,
            local_archive_path=body.local_archive_path,
            local_extracted_path=body.local_extracted_path,
            size_bytes=body.size_bytes,
            extracted_size_bytes=body.extracted_size_bytes,
            checksum_sha256=body.checksum_sha256,
            data_root_path=body.data_root_path,
            last_error=body.last_error,
            last_used_at=body.last_used_at,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.post(
    "/api/admin/datasets/{dataset_version_id}/worker-delete",
    dependencies=[Depends(require_admin_auth)],
)
def api_admin_request_worker_dataset_delete(
    dataset_version_id: str,
    body: WorkerDeleteRequest,
    conn: Connection = Depends(get_connection),
) -> Dict[str, int]:
    try:
        updated = request_worker_cache_delete(conn, dataset_version_id, body.worker_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {"updated": updated}


@app.patch(
    "/api/admin/workers/{worker_id}/concurrency",
    dependencies=[Depends(require_admin_auth)],
)
def api_admin_update_worker_concurrency(
    worker_id: str,
    body: WorkerConcurrencyRequest,
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        return update_worker_concurrency(
            conn,
            worker_id,
            body.max_concurrent_tasks,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.patch(
    "/api/admin/workers/{worker_id}/settings",
    dependencies=[Depends(require_admin_auth)],
)
def api_admin_update_worker_settings(
    worker_id: str,
    body: WorkerSettingsRequest,
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        return update_worker_settings(
            conn,
            worker_id,
            max_concurrent_tasks=body.max_concurrent_tasks,
            job_root=body.job_root,
            dataset_roots=[
                root.model_dump() if hasattr(root, "model_dump") else root.dict()
                for root in body.dataset_roots
            ]
            if body.dataset_roots is not None
            else None,
            reserve_overrides=(
                body.reserve_overrides.model_dump(exclude_none=True)
                if hasattr(body.reserve_overrides, "model_dump")
                else body.reserve_overrides.dict(exclude_none=True)
                if body.reserve_overrides is not None
                else None
            ),
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.delete(
    "/api/admin/datasets/{dataset_version_id}",
    dependencies=[Depends(require_admin_auth)],
)
def api_admin_delete_dataset(
    dataset_version_id: str,
    conn: Connection = Depends(get_connection),
) -> Dict[str, str]:
    try:
        delete_dataset_version(conn, dataset_version_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {"status": "ok"}


@app.get(
    "/api/admin/tasks/{task_id}/artifacts/{artifact_id}/download",
    dependencies=[Depends(require_admin_auth)],
)
def api_admin_download_task_artifact(
    task_id: str,
    artifact_id: str,
    conn: Connection = Depends(get_connection),
) -> FileResponse:
    try:
        artifact = get_artifact(conn, artifact_id)
        if artifact["task_id"] != task_id:
            raise ArtifactNotFound(artifact_id)
        path = get_uploaded_artifact_path(conn, artifact_id)
    except Exception as exc:
        raise map_store_error(exc) from exc
    return FileResponse(
        path,
        media_type=artifact.get("content_type") or "application/octet-stream",
        filename=artifact["display_name"],
    )


@app.post(
    "/api/worker/tasks/{task_id}/artifacts",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_create_artifact(
    task_id: str,
    body: CreateArtifactRequest,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        get_worker_for_api(conn, body.worker_id, worker_token)
        assert_task_owned_by_worker(conn, task_id, body.worker_id, body.lease_id)
        return create_artifact_record(
            conn,
            task_id=task_id,
            worker_id=body.worker_id,
            lease_id=body.lease_id,
            relative_path=body.relative_path,
            title=body.title,
            description=body.description,
            meaning=body.meaning,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            sha256=body.sha256,
            required=body.required,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.put(
    "/api/worker/tasks/{task_id}/artifacts/{artifact_id}/content",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_upload_artifact_content(
    task_id: str,
    artifact_id: str,
    content: bytes = Body(...),
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        artifact = get_artifact(conn, artifact_id)
        if artifact["task_id"] != task_id:
            raise ArtifactConflict("Artifact does not belong to this task")
        get_worker_for_api(conn, artifact["worker_id"], worker_token)
        assert_task_owned_by_worker(
            conn,
            task_id,
            artifact["worker_id"],
            artifact["lease_id"],
        )
        return store_artifact_content(conn, artifact_id, content)
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.get(
    "/api/worker/tasks/{task_id}/artifacts/{artifact_id}/upload-status",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_artifact_upload_status(
    task_id: str,
    artifact_id: str,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        artifact = get_artifact(conn, artifact_id)
        if artifact["task_id"] != task_id:
            raise ArtifactConflict("Artifact does not belong to this task")
        get_worker_for_api(conn, artifact["worker_id"], worker_token)
        assert_task_owned_by_worker(
            conn,
            task_id,
            artifact["worker_id"],
            artifact["lease_id"],
        )
        return artifact_upload_status(conn, artifact_id)
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.put(
    "/api/worker/tasks/{task_id}/artifacts/{artifact_id}/chunks/{offset}",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_upload_artifact_chunk(
    task_id: str,
    artifact_id: str,
    offset: int,
    content: bytes = Body(...),
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        artifact = get_artifact(conn, artifact_id)
        if artifact["task_id"] != task_id:
            raise ArtifactConflict("Artifact does not belong to this task")
        get_worker_for_api(conn, artifact["worker_id"], worker_token)
        assert_task_owned_by_worker(
            conn,
            task_id,
            artifact["worker_id"],
            artifact["lease_id"],
        )
        return store_artifact_chunk(conn, artifact_id, offset, content)
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.post(
    "/api/worker/tasks/{task_id}/artifacts/{artifact_id}/complete",
    dependencies=[Depends(require_worker_auth)],
)
def api_worker_complete_artifact_upload(
    task_id: str,
    artifact_id: str,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, Any]:
    try:
        artifact = get_artifact(conn, artifact_id)
        if artifact["task_id"] != task_id:
            raise ArtifactConflict("Artifact does not belong to this task")
        get_worker_for_api(conn, artifact["worker_id"], worker_token)
        assert_task_owned_by_worker(
            conn,
            task_id,
            artifact["worker_id"],
            artifact["lease_id"],
        )
        return complete_artifact_upload(conn, artifact_id)
    except Exception as exc:
        raise map_store_error(exc) from exc


@app.post(
    "/api/worker/tasks/{task_id}/success",
    dependencies=[Depends(require_worker_auth)],
)
def api_report_success(
    task_id: str,
    body: SuccessRequest,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, str]:
    try:
        get_worker_for_api(conn, body.worker_id, worker_token)
        report_success(
            conn,
            task_id,
            body.worker_id,
            body.lease_id,
            body.result,
            body.logs,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {"status": "ok"}


@app.post(
    "/api/worker/tasks/{task_id}/failed",
    dependencies=[Depends(require_worker_auth)],
)
def api_report_failed(
    task_id: str,
    body: FailedRequest,
    worker_token: str = Depends(require_worker_auth),
    conn: Connection = Depends(get_connection),
) -> Dict[str, str]:
    try:
        get_worker_for_api(conn, body.worker_id, worker_token)
        report_failed(
            conn,
            task_id,
            body.worker_id,
            body.lease_id,
            body.error,
            body.logs,
            error_code=body.error_code,
        )
    except Exception as exc:
        raise map_store_error(exc) from exc
    return {"status": "ok"}
