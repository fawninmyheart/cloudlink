import json
import http.client
import random
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class ApiRequestError(Exception):
    def __init__(
        self,
        message: str,
        *,
        method: str,
        path: str,
        attempt: int,
        elapsed_seconds: float,
        status_code: Optional[int] = None,
        original: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.path = path
        self.attempt = attempt
        self.elapsed_seconds = elapsed_seconds
        self.status_code = status_code
        self.original = original


class WorkerApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        worker_secret: str,
        default_timeout: float,
        default_retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float = 15,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_secret = worker_secret
        self.default_timeout = default_timeout
        self.default_retries = default_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.log = log or (lambda _message: None)

    def get_json(
        self,
        path: str,
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
        retry_max_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._json_request(
            "GET",
            path,
            None,
            True,
            timeout,
            retries,
            retry_base_seconds,
            retry_max_seconds,
        )

    def post_json(
        self,
        path: str,
        body: Dict[str, Any],
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
        retry_max_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            path,
            body,
            True,
            timeout,
            retries,
            retry_base_seconds,
            retry_max_seconds,
        )

    def probe_json(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        auth: bool = True,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
        retry_max_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._json_request(
            method,
            path,
            body,
            auth,
            timeout,
            retries,
            retry_base_seconds,
            retry_max_seconds,
        )

    def get_text(
        self,
        path: str,
        *,
        auth: bool = True,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
        retry_max_seconds: Optional[float] = None,
    ) -> str:
        return self._text_request(
            "GET",
            path,
            auth,
            timeout,
            retries,
            retry_base_seconds,
            retry_max_seconds,
        )

    def download_to_path(
        self,
        path: str,
        target: Path,
        *,
        timeout: float,
        retries: int,
    ) -> None:
        def run(request_timeout: float) -> None:
            request = urllib.request.Request(
                f"{self.base_url}{path}",
                method="GET",
                headers=self._headers(auth=True, json_body=False),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                with target.open("wb") as file:
                    shutil.copyfileobj(response, file)

        self._with_retries(
            "GET",
            path,
            run,
            timeout,
            retries,
            self.retry_base_seconds,
            self.retry_max_seconds,
        )

    def put_bytes(
        self,
        path: str,
        data: bytes,
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
        retry_max_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        def run(request_timeout: float) -> Dict[str, Any]:
            request = urllib.request.Request(
                f"{self.base_url}{path}",
                data=data,
                method="PUT",
                headers={
                    **self._headers(auth=True, json_body=False),
                    "Content-Type": "application/octet-stream",
                },
            )
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}

        return self._with_retries(
            "PUT",
            path,
            run,
            timeout if timeout is not None else self.default_timeout,
            retries if retries is not None else self.default_retries,
            retry_base_seconds if retry_base_seconds is not None else self.retry_base_seconds,
            retry_max_seconds if retry_max_seconds is not None else self.retry_max_seconds,
        )

    def _json_request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]],
        auth: bool,
        timeout: Optional[float],
        retries: Optional[int],
        retry_base_seconds: Optional[float],
        retry_max_seconds: Optional[float],
    ) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None

        def run(request_timeout: float) -> Dict[str, Any]:
            request = urllib.request.Request(
                f"{self.base_url}{path}",
                data=data,
                method=method,
                headers=self._headers(auth=auth, json_body=body is not None),
            )
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}

        return self._with_retries(
            method,
            path,
            run,
            timeout if timeout is not None else self.default_timeout,
            retries if retries is not None else self.default_retries,
            retry_base_seconds if retry_base_seconds is not None else self.retry_base_seconds,
            retry_max_seconds if retry_max_seconds is not None else self.retry_max_seconds,
        )

    def _text_request(
        self,
        method: str,
        path: str,
        auth: bool,
        timeout: Optional[float],
        retries: Optional[int],
        retry_base_seconds: Optional[float],
        retry_max_seconds: Optional[float],
    ) -> str:
        def run(request_timeout: float) -> str:
            request = urllib.request.Request(
                f"{self.base_url}{path}",
                method=method,
                headers=self._headers(auth=auth, json_body=False),
            )
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                return response.read().decode("utf-8", errors="replace")

        return self._with_retries(
            method,
            path,
            run,
            timeout if timeout is not None else self.default_timeout,
            retries if retries is not None else self.default_retries,
            retry_base_seconds if retry_base_seconds is not None else self.retry_base_seconds,
            retry_max_seconds if retry_max_seconds is not None else self.retry_max_seconds,
        )

    def _headers(self, *, auth: bool, json_body: bool) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if auth:
            headers["Authorization"] = f"Bearer {self.worker_secret}"
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _with_retries(
        self,
        method: str,
        path: str,
        operation: Callable[[float], Any],
        timeout: float,
        retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> Any:
        max_attempts = retries + 1
        last_error: Optional[BaseException] = None
        started_all = time.perf_counter()
        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            try:
                result = operation(timeout)
                elapsed = time.perf_counter() - started
                if attempt > 1:
                    self.log(
                        f"api ok method={method} endpoint={path} "
                        f"attempt={attempt} elapsed={elapsed:.2f}s"
                    )
                return result
            except Exception as exc:
                last_error = exc
                elapsed = time.perf_counter() - started
                status_code = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                self.log(
                    f"api error method={method} endpoint={path} attempt={attempt} "
                    f"elapsed={elapsed:.2f}s error_type={type(exc).__name__} "
                    f"error={short_error(exc)}"
                )
                if attempt >= max_attempts or not is_transient_error(exc):
                    total_elapsed = time.perf_counter() - started_all
                    raise ApiRequestError(
                        (
                            f"{method} {path} failed after {attempt} attempt(s): "
                            f"{short_error(exc)}"
                        ),
                        method=method,
                        path=path,
                        attempt=attempt,
                        elapsed_seconds=total_elapsed,
                        status_code=status_code,
                        original=exc,
                    ) from exc
                time.sleep(retry_delay_seconds(attempt, retry_base_seconds, retry_max_seconds))
        raise ApiRequestError(
            f"{method} {path} failed",
            method=method,
            path=path,
            attempt=max_attempts,
            elapsed_seconds=time.perf_counter() - started_all,
            original=last_error,
        )


def retry_delay_seconds(attempt: int, base_seconds: float, max_seconds: float) -> float:
    delay = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    jitter_ceiling = min(delay * 0.1, max(0.0, max_seconds - delay))
    return delay + random.uniform(0, jitter_ceiling)


def is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {429, 500, 502, 503, 504}
    if isinstance(
        exc,
        (
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
        ),
    ):
        return True
    message = str(exc).lower()
    return "timed out" in message or "ssl" in message or "eof" in message


def short_error(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:240]
