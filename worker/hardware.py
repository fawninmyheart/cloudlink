from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from os import cpu_count
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    from app.resource_model import subtract_reserved_profile
    from app.version import CLOUDLINK_VERSION
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.resource_model import subtract_reserved_profile
    from app.version import CLOUDLINK_VERSION


def disk_usage(path: Path) -> Tuple[int, int]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
    except OSError:
        return 0, 0
    total, _used, free = usage
    total = int(total)
    free = int(free)
    if sys.platform == "darwin":
        macos_usage = _macos_foundation_disk_usage(path)
        if macos_usage is not None:
            macos_total, macos_free = macos_usage
            total = max(total, int(macos_total or 0))
            free = max(free, int(macos_free or 0))
    return total, min(free, total)


def _macos_foundation_disk_usage(path: Path) -> Optional[Tuple[int, int]]:
    try:
        import ctypes
        import ctypes.util

        objc_path = ctypes.util.find_library("objc")
        foundation_path = ctypes.util.find_library("Foundation")
        if not objc_path or not foundation_path:
            return None
        objc = ctypes.CDLL(objc_path)
        foundation = ctypes.CDLL(foundation_path)

        objc_get_class = objc.objc_getClass
        objc_get_class.restype = ctypes.c_void_p
        objc_get_class.argtypes = [ctypes.c_char_p]
        sel_register_name = objc.sel_registerName
        sel_register_name.restype = ctypes.c_void_p
        sel_register_name.argtypes = [ctypes.c_char_p]

        def cls(name: str) -> int:
            return int(objc_get_class(name.encode("utf-8")) or 0)

        def sel(name: str) -> int:
            return int(sel_register_name(name.encode("utf-8")) or 0)

        send_id = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc))
        send_id_c_string = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
        )(("objc_msgSend", objc))
        send_id_id = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc))
        send_void = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc))
        send_void_id = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc))
        send_id_id_ptr = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )(("objc_msgSend", objc))
        send_unsigned_long_long = ctypes.CFUNCTYPE(
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc))

        pool_class = cls("NSAutoreleasePool")
        pool = send_id(send_id(pool_class, sel("alloc")), sel("init")) if pool_class else 0
        try:
            ns_string = cls("NSString")
            ns_url = cls("NSURL")
            ns_array = cls("NSMutableArray")
            if not ns_string or not ns_url or not ns_array:
                return None

            path_string = send_id_c_string(
                ns_string,
                sel("stringWithUTF8String:"),
                str(path).encode("utf-8"),
            )
            url = send_id_id(ns_url, sel("fileURLWithPath:"), path_string)
            keys = send_id(ns_array, sel("array"))
            if not url or not keys:
                return None

            key_names = [
                "NSURLVolumeTotalCapacityKey",
                "NSURLVolumeAvailableCapacityKey",
                "NSURLVolumeAvailableCapacityForImportantUsageKey",
                "NSURLVolumeAvailableCapacityForOpportunisticUsageKey",
            ]
            key_objects = []
            for name in key_names:
                try:
                    key = ctypes.c_void_p.in_dll(foundation, name).value
                except ValueError:
                    key = None
                if key:
                    key_objects.append((name, key))
                    send_void_id(keys, sel("addObject:"), key)
            if not key_objects:
                return None

            error = ctypes.c_void_p()
            values = send_id_id_ptr(
                url,
                sel("resourceValuesForKeys:error:"),
                keys,
                ctypes.byref(error),
            )
            if not values:
                return None

            capacities: Dict[str, int] = {}
            for name, key in key_objects:
                number = send_id_id(values, sel("objectForKey:"), key)
                if number:
                    capacities[name] = int(
                        send_unsigned_long_long(number, sel("unsignedLongLongValue"))
                    )
            total = capacities.get("NSURLVolumeTotalCapacityKey", 0)
            free = max(
                capacities.get("NSURLVolumeAvailableCapacityKey", 0),
                capacities.get("NSURLVolumeAvailableCapacityForImportantUsageKey", 0),
                capacities.get("NSURLVolumeAvailableCapacityForOpportunisticUsageKey", 0),
            )
            if total <= 0:
                return None
            return total, min(free, total)
        finally:
            if pool:
                send_void(pool, sel("drain"))
    except Exception:
        return None


def detect_total_memory_bytes() -> Optional[int]:
    if sys.platform == "darwin":
        return _sysctl_int("hw.memsize") or _sysconf_total_memory_bytes()
    if sys.platform.startswith("linux"):
        meminfo = _linux_meminfo()
        total_kb = meminfo.get("MemTotal")
        return total_kb * 1024 if total_kb is not None else _sysconf_total_memory_bytes()
    return _sysconf_total_memory_bytes()


def _sysconf_total_memory_bytes() -> Optional[int]:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    try:
        return int(pages) * int(page_size)
    except (TypeError, ValueError):
        return None


def detect_available_memory_bytes() -> Optional[int]:
    if sys.platform.startswith("linux"):
        meminfo = _linux_meminfo()
        available_kb = meminfo.get("MemAvailable") or meminfo.get("MemFree")
        return available_kb * 1024 if available_kb is not None else None
    if sys.platform == "darwin":
        # macOS exposes precise available memory through vm_stat, but the format
        # varies by release. Keep collection conservative instead of brittle.
        return None
    return None


def detect_gpu_devices() -> list[Dict[str, Any]]:
    if sys.platform.startswith("linux"):
        return _detect_nvidia_gpus()
    if sys.platform == "darwin":
        return _detect_macos_gpus()
    return []


def _sysctl_int(name: str) -> Optional[int]:
    try:
        output = subprocess.check_output(
            ["sysctl", "-n", name],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except Exception:
        return None
    try:
        return int(output)
    except ValueError:
        return None


def _linux_meminfo() -> Dict[str, int]:
    values: Dict[str, int] = {}
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue
    return values


def _detect_nvidia_gpus() -> list[Dict[str, Any]]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        return []
    devices = []
    for line in output.splitlines():
        if not line.strip() or "," not in line:
            continue
        name, memory_mib = [part.strip() for part in line.split(",", 1)]
        try:
            memory_bytes = int(memory_mib) * 1024**2
        except ValueError:
            memory_bytes = 0
        devices.append({"name": name or "NVIDIA GPU", "memory_total_bytes": memory_bytes})
    return devices


def _detect_macos_gpus() -> list[Dict[str, Any]]:
    try:
        output = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        return []
    devices = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Chipset Model:"):
            devices.append(
                {
                    "name": stripped.split(":", 1)[1].strip() or "Apple GPU",
                    "memory_total_bytes": 0,
                }
            )
    return devices


def build_runtime_profile(
    *,
    worker_id: str,
    job_root: Path,
    dataset_root: Path,
    dataset_roots: Optional[List[Dict[str, Any]]] = None,
    python_runtime: Optional[Path] = None,
) -> Dict[str, Any]:
    return {
        "worker_id": worker_id,
        "cloudlink_version": os.getenv("CLOUDLINK_VERSION", CLOUDLINK_VERSION).strip()
        or CLOUDLINK_VERSION,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "python_runtime": str(python_runtime or Path(sys.executable)),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "system": platform.system(),
        "job_root": str(job_root),
        "dataset_root": str(dataset_root),
        "dataset_roots": dataset_roots
        or [{"path": str(dataset_root), "mode": "active"}],
    }


def build_capacity_state(
    hardware_profile: Mapping[str, Any],
    *,
    memory_available_bytes: Optional[int],
    job_disk_free_bytes: Optional[int],
    dataset_disk_free_bytes: Optional[int],
) -> Dict[str, Any]:
    scheduler = dict(hardware_profile.get("scheduler") or {})
    reserve = dict(hardware_profile.get("reserve") or {})

    def free_or_scheduler(
        free_value: Optional[int],
        reserve_key: str,
        scheduler_key: str,
    ) -> int:
        scheduler_value = int(scheduler.get(scheduler_key) or 0)
        if free_value is None:
            return scheduler_value
        visible_free = max(0, int(free_value) - int(reserve.get(reserve_key) or 0))
        return min(scheduler_value, visible_free)

    return {
        "cpu_cores": int(float(scheduler.get("cpu_cores") or 0)),
        "memory_bytes": free_or_scheduler(
            memory_available_bytes,
            "memory_bytes",
            "memory_bytes",
        ),
        "job_disk_bytes": free_or_scheduler(
            job_disk_free_bytes,
            "job_disk_bytes",
            "job_disk_bytes",
        ),
        "dataset_disk_bytes": free_or_scheduler(
            dataset_disk_free_bytes,
            "dataset_disk_bytes",
            "dataset_disk_bytes",
        ),
        "gpu_devices": scheduler.get("gpu_devices") or [],
    }


def collect_worker_profiles(
    *,
    job_root: Path,
    dataset_root: Path,
    dataset_roots: Optional[List[Dict[str, Any]]] = None,
    worker_id: str = "",
    python_runtime: Optional[Path] = None,
    reserve_overrides: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    job_total, job_free = disk_usage(job_root)
    dataset_total, dataset_free = disk_usage(dataset_root)
    raw_profile = {
        "cpu_logical_cores": cpu_count() or 0,
        "memory_total_bytes": detect_total_memory_bytes() or 0,
        "job_disk_total_bytes": job_total,
        "job_disk_free_bytes": job_free,
        "dataset_disk_total_bytes": dataset_total,
        "dataset_disk_free_bytes": dataset_free,
        "gpu_devices": detect_gpu_devices(),
    }
    hardware_profile = subtract_reserved_profile(raw_profile, reserve_overrides)
    runtime_profile = build_runtime_profile(
        worker_id=worker_id,
        job_root=job_root,
        dataset_root=dataset_root,
        dataset_roots=dataset_roots,
        python_runtime=python_runtime,
    )
    capacity_state = build_capacity_state(
        hardware_profile,
        memory_available_bytes=detect_available_memory_bytes(),
        job_disk_free_bytes=job_free,
        dataset_disk_free_bytes=dataset_free,
    )
    return hardware_profile, runtime_profile, capacity_state
