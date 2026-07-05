from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple


GIB = 1024**3


class ResourceValidationError(ValueError):
    pass


def _number(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ResourceValidationError(f"{field} must be a number") from exc
    if parsed < 0:
        raise ResourceValidationError(f"{field} must be non-negative")
    return parsed


def _int(value: Any, field: str) -> int:
    parsed = _number(value, field)
    if parsed != int(parsed):
        raise ResourceValidationError(f"{field} must be an integer")
    return int(parsed)


def _ceil_int(value: Any, field: str) -> int:
    return int(math.ceil(_number(value, field)))


def _ceil_gib_bytes(value: float) -> int:
    if value <= 0:
        return 0
    return int(math.ceil(value / GIB) * GIB)


def normalize_resource_request(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    source = dict(value or {})
    gpu_source = source.get("gpu") or {}
    if not isinstance(gpu_source, Mapping):
        raise ResourceValidationError("gpu must be an object")

    concurrency_slots = _int(source.get("concurrency_slots", 1), "concurrency_slots")
    if concurrency_slots < 1:
        raise ResourceValidationError("concurrency_slots must be at least 1")

    return {
        "cpu_cores": _ceil_int(source.get("cpu_cores", 0), "cpu_cores"),
        "memory_bytes": _int(source.get("memory_bytes", 0), "memory_bytes"),
        "job_disk_bytes": _int(source.get("job_disk_bytes", 0), "job_disk_bytes"),
        "dataset_disk_bytes": _int(
            source.get("dataset_disk_bytes", 0),
            "dataset_disk_bytes",
        ),
        "expected_runtime_seconds": _int(
            source.get("expected_runtime_seconds", 0),
            "expected_runtime_seconds",
        ),
        "concurrency_slots": concurrency_slots,
        "gpu": {
            "required": bool(gpu_source.get("required", False)),
            "count": _int(gpu_source.get("count", 0), "gpu.count"),
            "memory_bytes": _int(
                gpu_source.get("memory_bytes", 0),
                "gpu.memory_bytes",
            ),
        },
    }


def _default_reserve(raw: Mapping[str, Any]) -> Dict[str, Any]:
    cpu_total = _int(raw.get("cpu_logical_cores", 0), "cpu_logical_cores")
    memory_total = _int(raw.get("memory_total_bytes", 0), "memory_total_bytes")
    job_disk_total = _int(raw.get("job_disk_total_bytes", 0), "job_disk_total_bytes")
    dataset_disk_total = _int(
        raw.get("dataset_disk_total_bytes", job_disk_total),
        "dataset_disk_total_bytes",
    )
    gpu_memory_values = [
        _int(device.get("memory_total_bytes", 0), "gpu.memory_total_bytes")
        for device in raw.get("gpu_devices", []) or []
        if isinstance(device, Mapping)
    ]
    gpu_memory_total = max(gpu_memory_values) if gpu_memory_values else 0
    return {
        "cpu_cores": max(1 if cpu_total else 0, int(math.ceil(cpu_total * 0.2))),
        "memory_bytes": max(
            4 * GIB if memory_total else 0,
            _ceil_gib_bytes(memory_total * 0.2),
        ),
        "job_disk_bytes": max(
            20 * GIB if job_disk_total else 0,
            _ceil_gib_bytes(job_disk_total * 0.1),
        ),
        "dataset_disk_bytes": max(
            20 * GIB if dataset_disk_total else 0,
            _ceil_gib_bytes(dataset_disk_total * 0.1),
        ),
        "gpu_memory_bytes": max(
            1 * GIB if gpu_memory_total else 0,
            _ceil_gib_bytes(gpu_memory_total * 0.1),
        ),
    }


def _reserve_value(
    reserve: Mapping[str, Any],
    defaults: Mapping[str, Any],
    key: str,
    alias: Optional[str] = None,
) -> Any:
    if key in reserve:
        return reserve[key]
    if alias and alias in reserve:
        return reserve[alias]
    return defaults[key]


def _subtract(total: Any, reserve: Any, field: str) -> Any:
    if total is None:
        return None
    total_value = _number(total, field)
    reserve_value = _number(reserve, f"{field}.reserve")
    return max(0, total_value - reserve_value)


def _subtract_int(total: Any, reserve: Any, field: str) -> Optional[int]:
    if total is None:
        return None
    total_value = _int(total, field)
    reserve_value = _int(reserve, f"{field}.reserve")
    return max(0, total_value - reserve_value)


def subtract_reserved_profile(
    raw_profile: Mapping[str, Any],
    reserve_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    reserve_source = dict(reserve_overrides or {})
    defaults = _default_reserve(raw_profile)
    reserve = {
        "cpu_cores": _ceil_int(
            _reserve_value(reserve_source, defaults, "cpu_cores"),
            "reserve.cpu_cores",
        ),
        "memory_bytes": _int(
            _reserve_value(reserve_source, defaults, "memory_bytes"),
            "reserve.memory_bytes",
        ),
        "job_disk_bytes": _int(
            _reserve_value(reserve_source, defaults, "job_disk_bytes", "disk_bytes"),
            "reserve.job_disk_bytes",
        ),
        "dataset_disk_bytes": _int(
            _reserve_value(
                reserve_source,
                defaults,
                "dataset_disk_bytes",
                "disk_bytes",
            ),
            "reserve.dataset_disk_bytes",
        ),
        "gpu_memory_bytes": _int(
            _reserve_value(reserve_source, defaults, "gpu_memory_bytes"),
            "reserve.gpu_memory_bytes",
        ),
    }
    gpu_devices = []
    for device in raw_profile.get("gpu_devices", []) or []:
        if not isinstance(device, Mapping):
            continue
        total = device.get("memory_total_bytes")
        gpu_devices.append(
            {
                "name": device.get("name") or "GPU",
                "memory_bytes": int(_subtract(total, reserve["gpu_memory_bytes"], "gpu.memory") or 0),
            }
        )

    scheduler = {
        "cpu_cores": _subtract_int(
            raw_profile.get("cpu_logical_cores", 0),
            reserve["cpu_cores"],
            "cpu_logical_cores",
        ),
        "memory_bytes": int(
            _subtract(
                raw_profile.get("memory_total_bytes", 0),
                reserve["memory_bytes"],
                "memory_total_bytes",
            )
            or 0
        ),
        "job_disk_bytes": int(
            _subtract(
                raw_profile.get("job_disk_total_bytes", 0),
                reserve["job_disk_bytes"],
                "job_disk_total_bytes",
            )
            or 0
        ),
        "dataset_disk_bytes": int(
            _subtract(
                raw_profile.get(
                    "dataset_disk_total_bytes",
                    raw_profile.get("job_disk_total_bytes", 0),
                ),
                reserve["dataset_disk_bytes"],
                "dataset_disk_total_bytes",
            )
            or 0
        ),
        "gpu_devices": gpu_devices,
    }
    return {"raw": dict(raw_profile), "reserve": reserve, "scheduler": scheduler}


def fits_capacity(
    request: Mapping[str, Any],
    capacity: Mapping[str, Any],
) -> Tuple[bool, List[Dict[str, Any]]]:
    shortages: List[Dict[str, Any]] = []

    for field in ("cpu_cores", "memory_bytes", "job_disk_bytes", "dataset_disk_bytes"):
        requested = request.get(field, 0)
        available = capacity.get(field)
        if requested and available is None:
            shortages.append(
                {"resource": field, "requested": requested, "available": None}
            )
        elif requested and float(available) < float(requested):
            shortages.append(
                {"resource": field, "requested": requested, "available": available}
            )

    gpu_request = request.get("gpu") or {}
    if gpu_request.get("required"):
        requested_count = int(gpu_request.get("count") or 1)
        requested_memory = int(gpu_request.get("memory_bytes") or 0)
        matching = [
            device
            for device in capacity.get("gpu_devices", []) or []
            if int(device.get("memory_bytes") or 0) >= requested_memory
        ]
        if len(matching) < requested_count:
            max_memory = max(
                [int(device.get("memory_bytes") or 0) for device in capacity.get("gpu_devices", []) or []],
                default=0,
            )
            shortages.append(
                {
                    "resource": "gpu",
                    "requested": {
                        "count": requested_count,
                        "memory_bytes": requested_memory,
                    },
                    "available": {
                        "count": len(matching),
                        "memory_bytes": max_memory,
                    },
                }
            )

    return not shortages, shortages
