"""Managed OpenCode installation and discovery for first-time Siming users."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.ai.local_cli_adapter import (
    OPENCODE_MODELS,
    discover_local_cli_models,
    hidden_subprocess_kwargs,
)
from app.architecture.uow import commit_session
from app.services.application_settings import app_home as _app_home
from app.services.opencode_activation import (
    activation_failure_kind as _activation_failure_kind,
)
from app.services.opencode_activation import (
    probe_free_models as _probe_free_models,
)
from app.services.opencode_activation import (
    save_activated_config as _save_activated_config,
)
from app.services.opencode_activation import (
    save_readiness_failure as _save_activation_readiness_failure,
)
from app.services.opencode_activation import (
    test_model as _activation_test_model,
)
from app.services.opencode_release_catalog import managed_windows_release
from app.services.opencode_command_runtime import (
    command_version as _probe_command_version,
    free_model_options as _free_model_options,
    is_free_model as is_free_opencode_model,
    resolve_command as _resolve_command,
    subprocess_command as _subprocess_command,
)
from app.services.windows_user_path import (
    broadcast_environment_change as _broadcast_environment_change,
    configure_user_path as _configure_user_path,
    path_integration_supported as _path_integration_supported,
    read_current_user_path as _read_current_user_path,
    user_path_status as _user_path_status,
    windows_path_contains as _windows_path_contains,
    write_current_user_path as _write_current_user_path,
)

OPENCODE_RELEASES_URL = "https://github.com/anomalyco/opencode/releases/latest"
OPENCODE_INSTALL_DOCS_URL = "https://opencode.ai/docs/#install"
OPENCODE_MODELS_DOCS_URL = "https://opencode.ai/docs/zen"
OPENCODE_AUTH_URL = "https://opencode.ai/auth"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_PROBE_BYTES = 256 * 1024
DOWNLOAD_PROBE_TIMEOUT = 6
DOWNLOAD_SLOW_WINDOW_SECONDS = 12
DOWNLOAD_MIN_SWITCH_RATE = 128 * 1024
DOWNLOAD_PROGRESS_INTERVAL_SECONDS = 1
INSPECTION_CACHE_SECONDS = 30
ACTIVATION_TEST_TIMEOUT = 60


async def _test_opencode_model(command: str, model: str) -> None:
    await _activation_test_model(command, model, timeout_seconds=ACTIVATION_TEST_TIMEOUT)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_activation_start_lock = threading.Lock()
_user_path_lock = threading.Lock()
_inspection_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_inspection_cache_lock = threading.Lock()
_auth_sessions_lock = threading.Lock()


@dataclass
class _ManagedAuthSession:
    process: Any
    credential: str = ""


@dataclass(frozen=True)
class _DownloadSource:
    label: str
    url: str
    archive_format: str
    digest_algorithm: str
    expected_digest: str
    expected_size: int

    @property
    def artifact_key(self) -> str:
        return f"{self.digest_algorithm}-{self.expected_digest[:16]}"


@dataclass(frozen=True)
class _DownloadProbe:
    source: _DownloadSource
    available: bool
    bytes_per_second: float = 0
    latency_seconds: float = 0
    error: str | None = None


class _SlowDownloadSource(RuntimeError):
    """Signal that a resumable download should continue on another source."""


_auth_sessions: dict[str, _ManagedAuthSession] = {}
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_AUTH_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_AUTH_CREDENTIAL_PROMPT_RE = re.compile(
    r"(?i)(paste|enter|input|provide).{0,40}(token|code|credential)|"
    r"(token|code|credential).{0,40}(paste|enter|input|provide)|"
    r"请输入.{0,20}(令牌|验证码|凭据)",
)


def managed_opencode_root() -> Path:
    return _app_home() / "managed-cli" / "opencode"


def managed_opencode_command() -> Path:
    return managed_opencode_root() / "bin" / "opencode.exe"


def managed_opencode_path_status(command: str | None = None) -> dict[str, Any]:
    return _user_path_status(
        command,
        managed_opencode_command(),
        supported=_path_integration_supported(),
        read_path=_read_current_user_path,
    )


def configure_managed_opencode_path(*, enabled: bool = True) -> dict[str, Any]:
    command = managed_opencode_command()
    with _user_path_lock:
        changes = _configure_user_path(
            command,
            enabled=enabled,
            supported=_path_integration_supported(),
            read_path=_read_current_user_path,
            write_path=_write_current_user_path,
            broadcast=_broadcast_environment_change,
        )
    clear_opencode_inspection_cache()
    return {
        **managed_opencode_path_status(str(command)),
        "changed": changes["registry_changed"] or changes["process_changed"],
    }


def resolve_opencode_command(preferred: str | None = None) -> str | None:
    return _resolve_command(preferred, managed_opencode_command())


def _command_version(command: str, *, timeout: int = 5) -> str | None:
    return _probe_command_version(command, timeout=timeout)


def _inspection_cache_key(command: str | None) -> tuple[str, int]:
    if not command:
        return ("", 0)
    try:
        modified = Path(command).stat().st_mtime_ns
    except OSError:
        modified = 0
    return (command, modified)


def clear_opencode_inspection_cache() -> None:
    with _inspection_cache_lock:
        _inspection_cache.clear()


def inspect_opencode(
    preferred_command: str | None = None,
    *,
    timeout: int = 8,
    refresh: bool = False,
) -> dict[str, Any]:
    command = resolve_opencode_command(preferred_command)
    cache_key = _inspection_cache_key(command)
    now = time.monotonic()
    if not refresh:
        with _inspection_cache_lock:
            cached = _inspection_cache.get(cache_key)
            if cached and now - cached[0] < INSPECTION_CACHE_SECONDS:
                result = deepcopy(cached[1])
                result["path_integration"] = managed_opencode_path_status(command)
                return result

    version = _command_version(command, timeout=min(max(timeout, 2), 6)) if command else None
    discovered = discover_local_cli_models("opencode_cli", command, timeout=timeout) if command else []
    model_source = "cli"
    if command and not discovered:
        discovered = [{"id": model, "display_name": model} for model in OPENCODE_MODELS]
        model_source = "fallback"
    free_models = _free_model_options(discovered)
    recommended = next((item["id"] for item in free_models if item["recommended"]), None)
    if not recommended and free_models:
        recommended = free_models[0]["id"]
    managed_root = managed_opencode_root().resolve()
    managed = False
    if command:
        try:
            Path(command).resolve().relative_to(managed_root)
            managed = True
        except (OSError, ValueError):
            managed = False
    result = {
        "installed": bool(command and version),
        "command": command,
        "version": version,
        "managed_by_siming": managed,
        "models": discovered,
        "model_source": model_source if command else "none",
        "free_models": free_models,
        "recommended_model": recommended,
        "platform_supported": os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64", "arm64", "aarch64"},
        "install_location": str(managed_opencode_command()),
        "path_integration": managed_opencode_path_status(command),
        "official_links": {
            "releases": OPENCODE_RELEASES_URL,
            "install_docs": OPENCODE_INSTALL_DOCS_URL,
            "model_docs": OPENCODE_MODELS_DOCS_URL,
        },
    }
    with _inspection_cache_lock:
        _inspection_cache.clear()
        _inspection_cache[cache_key] = (now, deepcopy(result))
    return result


def _latest_release_asset() -> tuple[str, dict[str, Any]]:
    return managed_windows_release()


def _set_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(changes)
        job["updated_at"] = datetime.now(UTC).isoformat()
        return dict(job)


def _mirror_urls(official_url: str, asset_name: str) -> list[str]:
    """Return operator-approved mirrors without trusting them for integrity."""
    configured = os.environ.get("SIMING_OPENCODE_MIRROR_URLS", "")
    urls = [official_url]
    for template in configured.split(";"):
        template = template.strip()
        if not template:
            continue
        candidate = template.replace("{url}", official_url).replace("{asset}", asset_name)
        if "{" not in candidate and candidate.startswith("https://") and candidate not in urls:
            urls.append(candidate)
    return urls


def _download_sources(asset: dict[str, Any]) -> list[_DownloadSource]:
    """Build verified source candidates, including operator-provided ZIP mirrors."""
    raw_sources = list(asset.get("download_sources") or [])
    official_url = str(asset.get("browser_download_url") or "").strip()
    if not raw_sources and official_url:
        raw_sources.append({
            "label": "GitHub 官方源",
            "url": official_url,
            "archive_format": "zip",
            "size": int(asset.get("size") or 0),
            "digest": str(asset.get("digest") or ""),
        })

    if official_url:
        official_source = next(
            (item for item in raw_sources if str(item.get("url") or "") == official_url),
            None,
        )
        if official_source:
            mirror_urls = _mirror_urls(
                official_url,
                str(asset.get("name") or ""),
            )[1:]
            for index, url in enumerate(mirror_urls, 1):
                raw_sources.append({
                    **official_source,
                    "label": f"自定义加速源 {index}",
                    "url": url,
                })

    sources: list[_DownloadSource] = []
    seen_urls: set[str] = set()
    for item in raw_sources:
        url = str(item.get("url") or "").strip()
        if urlparse(url).scheme.lower() != "https" or url in seen_urls:
            continue
        digest_value = str(item.get("digest") or "").strip().lower()
        algorithm, separator, digest = digest_value.partition(":")
        archive_format = str(item.get("archive_format") or "zip").strip().lower()
        if separator != ":" or algorithm not in {"sha256", "sha512"}:
            continue
        if archive_format not in {"zip", "tgz"}:
            continue
        expected_length = hashlib.new(algorithm).digest_size * 2
        invalid_hex = any(
            character not in "0123456789abcdef"
            for character in digest
        )
        if len(digest) != expected_length or invalid_hex:
            continue
        seen_urls.add(url)
        sources.append(_DownloadSource(
            label=str(item.get("label") or "备用下载源").strip() or "备用下载源",
            url=url,
            archive_format=archive_format,
            digest_algorithm=algorithm,
            expected_digest=digest,
            expected_size=max(0, int(item.get("size") or 0)),
        ))
    return sources


def _download_source_label(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host == "registry.npmmirror.com" or host.endswith(".npmmirror.com"):
        return "国内加速源"
    if host == "registry.npmjs.org" or host.endswith(".npmjs.org"):
        return "npm 官方源"
    if host == "github.com" or host.endswith(".githubusercontent.com"):
        return "GitHub 官方源"
    return "自定义加速源"


def _read_available_chunk(response: Any, size: int) -> bytes:
    """Prefer a single buffered read so slow trickles can be measured promptly."""
    read_once = getattr(response, "read1", None)
    if callable(read_once):
        return read_once(size)
    return response.read(size)


def _probe_download_source(source: _DownloadSource) -> _DownloadProbe:
    """Read a small range so source ordering reflects this computer's network."""
    started = time.monotonic()
    request = Request(
        source.url,
        headers={
            "User-Agent": "Siming-OpenCode-Onboarding",
            "Range": f"bytes=0-{DOWNLOAD_PROBE_BYTES - 1}",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urlopen(request, timeout=DOWNLOAD_PROBE_TIMEOUT) as response:
            chunks: list[bytes] = []
            received = 0
            deadline = started + DOWNLOAD_PROBE_TIMEOUT
            while received < DOWNLOAD_PROBE_BYTES and time.monotonic() < deadline:
                chunk = _read_available_chunk(response, DOWNLOAD_PROBE_BYTES - received)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            sample = b"".join(chunks)
        elapsed = max(0.001, time.monotonic() - started)
        if not sample:
            raise RuntimeError("测速没有收到数据")
        return _DownloadProbe(
            source=source,
            available=True,
            bytes_per_second=len(sample) / elapsed,
            latency_seconds=elapsed,
        )
    except Exception as exc:
        return _DownloadProbe(
            source=source,
            available=False,
            latency_seconds=max(0.001, time.monotonic() - started),
            error=str(exc),
        )


def _rank_download_sources(sources: list[_DownloadSource]) -> list[_DownloadProbe]:
    """Probe candidates in parallel and put the fastest reachable source first."""
    if len(sources) <= 1:
        return [_probe_download_source(source) for source in sources]
    source_order = {source.url: index for index, source in enumerate(sources)}
    probes: list[_DownloadProbe] = []
    with ThreadPoolExecutor(
        max_workers=min(4, len(sources)),
        thread_name_prefix="opencode-source",
    ) as executor:
        futures = {executor.submit(_probe_download_source, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                probes.append(future.result())
            except Exception as exc:  # pragma: no cover - the probe normally captures errors
                probes.append(_DownloadProbe(source=source, available=False, error=str(exc)))
    return sorted(
        probes,
        key=lambda probe: (
            not probe.available,
            -probe.bytes_per_second if probe.available else 0,
            probe.latency_seconds,
            source_order[probe.source.url],
        ),
    )


def _file_matches_digest(path: Path, algorithm: str, expected_digest: str) -> bool:
    digest = hashlib.new(algorithm)
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected_digest.lower()


def _download_asset_resumable(
    url: str,
    destination: Path,
    *,
    progress: Callable[[int, int], None],
    expected_sha256: str | None = None,
    expected_digest: str | None = None,
    digest_algorithm: str = "sha256",
    expected_size: int = 0,
) -> None:
    """Download with Range resume and verify the complete file afterwards."""
    verified_digest = str(expected_digest or expected_sha256 or "").lower()
    if not verified_digest:
        raise RuntimeError("OpenCode 下载源缺少构建时固定的文件摘要")
    if digest_algorithm not in {"sha256", "sha512"}:
        raise RuntimeError("OpenCode 下载源使用了不支持的摘要算法")
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.stat().st_size if destination.exists() else 0
    if expected_size and existing > expected_size:
        destination.unlink(missing_ok=True)
        existing = 0
    if existing and (not expected_size or existing == expected_size):
        if _file_matches_digest(destination, digest_algorithm, verified_digest):
            progress(existing, existing)
            return
        if expected_size and existing == expected_size:
            destination.unlink(missing_ok=True)
            existing = 0
    headers = {"User-Agent": "Siming-OpenCode-Onboarding"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=60) as response:
        content_range = str(response.headers.get("Content-Range") or "")
        partial = (
            existing > 0
            and getattr(response, "status", None) == 206
            and content_range.startswith(f"bytes {existing}-")
        )
        mode = "ab" if partial else "wb"
        downloaded = existing if partial else 0
        remaining = int(response.headers.get("Content-Length") or 0)
        total = expected_size or (downloaded + remaining if remaining else 0)
        with destination.open(mode) as output:
            last_reported = downloaded
            last_reported_at = time.monotonic()
            while True:
                chunk = _read_available_chunk(response, DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_reported_at >= DOWNLOAD_PROGRESS_INTERVAL_SECONDS:
                    progress(downloaded, total)
                    last_reported = downloaded
                    last_reported_at = now
            if downloaded != last_reported:
                progress(downloaded, total)

    actual_size = destination.stat().st_size
    if expected_size and actual_size != expected_size:
        if actual_size < expected_size:
            raise RuntimeError(
                f"连接提前结束（已下载 {actual_size}/{expected_size} 字节），进度已保留"
            )
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"下载文件大小异常（应为 {expected_size} 字节），已删除")
    if not _file_matches_digest(destination, digest_algorithm, verified_digest):
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"下载文件与 OpenCode 发布时固定的 {digest_algorithm.upper()} 不一致，已删除"
        )


def _extract_opencode(
    archive_path: Path,
    destination: Path,
    *,
    archive_format: str = "zip",
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".exe.new")
    if archive_format == "zip":
        with zipfile.ZipFile(archive_path) as archive:
            member = next(
                (
                    item for item in archive.infolist()
                    if not item.is_dir()
                    and PurePosixPath(item.filename).name.lower() == "opencode.exe"
                ),
                None,
            )
            if member is None:
                raise RuntimeError("OpenCode 官方安装包中没有找到 opencode.exe")
            with archive.open(member) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=DOWNLOAD_CHUNK_SIZE)
    elif archive_format == "tgz":
        with tarfile.open(archive_path, mode="r:gz") as archive:
            member = next(
                (
                    item for item in archive.getmembers()
                    if item.isfile()
                    and PurePosixPath(item.name).name.lower() == "opencode.exe"
                ),
                None,
            )
            if member is None:
                raise RuntimeError("OpenCode 官方 npm 包中没有找到 opencode.exe")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("OpenCode 官方 npm 包中的 opencode.exe 无法读取")
            with source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=DOWNLOAD_CHUNK_SIZE)
    else:
        raise RuntimeError(f"不支持的 OpenCode 安装包格式：{archive_format}")
    os.replace(temporary, destination)


def _download_part_path(root: Path, asset_name: str, source: _DownloadSource) -> Path:
    base_name = Path(asset_name).stem or "opencode-windows"
    return root / "downloads" / (
        f"{base_name}-{source.artifact_key}.{source.archive_format}.part"
    )


def _download_release_archive(
    root: Path,
    asset: dict[str, Any],
    *,
    on_event: Callable[[str, dict[str, Any]], None],
) -> tuple[Path, _DownloadSource, set[Path]]:
    """Select, download, and verify a release archive across safe sources."""
    sources = _download_sources(asset)
    if not sources:
        raise RuntimeError("OpenCode 发布目录中没有可校验的 HTTPS 下载源")
    on_event("probing", {"source_count": len(sources)})
    probes = _rank_download_sources(sources)
    ordered = [probe.source for probe in probes]
    probe_by_url = {probe.source.url: probe for probe in probes}
    part_paths = {
        _download_part_path(root, str(asset.get("name") or "opencode.zip"), source)
        for source in sources
    }
    errors: list[str] = []
    attempts = ordered * 2

    for attempt_index, source in enumerate(attempts):
        source_position = ordered.index(source) + 1
        can_switch = attempt_index < len(attempts) - 1
        probe = probe_by_url[source.url]
        archive_path = _download_part_path(
            root,
            str(asset.get("name") or "opencode.zip"),
            source,
        )
        start_bytes = archive_path.stat().st_size if archive_path.exists() else 0
        attempt_started = time.monotonic()
        on_event("source_selected", {
            "source": source,
            "source_position": source_position,
            "source_count": len(ordered),
            "round": attempt_index // len(ordered) + 1,
            "probe": probe,
            "resuming_bytes": start_bytes,
        })

        def on_progress(
            downloaded: int,
            total: int,
            *,
            started: float = attempt_started,
            initial_bytes: int = start_bytes,
            active_source: _DownloadSource = source,
            allow_switch: bool = can_switch,
        ) -> None:
            elapsed = max(0.1, time.monotonic() - started)
            transferred = max(0, downloaded - initial_bytes)
            rate = transferred / elapsed
            remaining = int((total - downloaded) / rate) if total and rate > 0 else None
            on_event("progress", {
                "source": active_source,
                "downloaded": downloaded,
                "total": total,
                "bytes_per_second": rate,
                "estimated_seconds_remaining": remaining,
            })
            if (
                allow_switch
                and elapsed >= DOWNLOAD_SLOW_WINDOW_SECONDS
                and rate < DOWNLOAD_MIN_SWITCH_RATE
            ):
                raise _SlowDownloadSource(
                    f"{active_source.label} 持续速度低于 "
                    f"{DOWNLOAD_MIN_SWITCH_RATE // 1024} KB/s"
                )

        try:
            _download_asset_resumable(
                source.url,
                archive_path,
                progress=on_progress,
                expected_digest=source.expected_digest,
                digest_algorithm=source.digest_algorithm,
                expected_size=source.expected_size,
            )
            return archive_path, source, part_paths
        except Exception as exc:
            errors.append(f"{source.label}（第 {attempt_index // len(ordered) + 1} 轮）：{exc}")
            if can_switch:
                on_event("switching", {
                    "source": source,
                    "reason": str(exc),
                    "next_source": attempts[attempt_index + 1],
                })

    raise RuntimeError("所有安全下载线路均未完成。" + "；".join(errors[-4:]))


def _install_worker(job_id: str) -> None:
    root = managed_opencode_root()
    try:
        _set_job(
            job_id,
            status="running",
            phase="checking_release",
            percent=2,
            message="正在读取 OpenCode 官方发行信息",
        )
        version, asset = _latest_release_asset()
        expected_sha256 = str(asset["digest"]).removeprefix("sha256:")
        root.mkdir(parents=True, exist_ok=True)

        def on_event(event: str, details: dict[str, Any]) -> None:
            if event == "probing":
                _set_job(
                    job_id,
                    phase="selecting_source",
                    percent=3,
                    message=f"正在测速 {details['source_count']} 条安全下载线路",
                )
            elif event == "source_selected":
                source = details["source"]
                _set_job(
                    job_id,
                    phase="downloading",
                    percent=5,
                    message=f"已选择{source.label}，正在下载 {version}",
                    download_url=source.url,
                    download_source=source.label,
                )
            elif event == "progress":
                downloaded = int(details["downloaded"])
                total = int(details["total"])
                fraction = downloaded / total if total else 0
                _set_job(
                    job_id,
                    percent=max(5, min(85, int(5 + fraction * 80))),
                    bytes_downloaded=downloaded,
                    bytes_total=total,
                    estimated_seconds_remaining=details["estimated_seconds_remaining"],
                    bytes_per_second=int(details["bytes_per_second"]),
                )
            elif event == "switching":
                _set_job(
                    job_id,
                    message=f"当前线路不稳定，正在切换到{details['next_source'].label}",
                )

        archive_path, source, part_paths = _download_release_archive(
            root,
            asset,
            on_event=on_event,
        )
        _set_job(job_id, phase="installing", percent=90, message="下载完成，正在解压到司命专用目录")
        command = managed_opencode_command()
        _extract_opencode(archive_path, command, archive_format=source.archive_format)
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)

        _set_job(job_id, phase="verifying", percent=96, message="正在检查 OpenCode 是否可以运行")
        inspected = inspect_opencode(str(managed_opencode_command()), timeout=15, refresh=True)
        if not inspected["installed"]:
            raise RuntimeError("OpenCode 已下载，但启动检查失败")
        metadata = {
            "version": version,
            "asset": asset["name"],
            "sha256": expected_sha256,
            "source": source.url,
            "source_label": source.label,
            "source_digest": f"{source.digest_algorithm}:{source.expected_digest}",
            "command": str(managed_opencode_command()),
            "installed_at": datetime.now(UTC).isoformat(),
        }
        metadata_path = root / "install.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        _set_job(
            job_id,
            status="completed",
            phase="completed",
            percent=100,
            message="OpenCode 已安装，可以选择免费模型",
            command=inspected["command"],
            version=inspected["version"] or version,
            free_models=inspected["free_models"],
            recommended_model=inspected["recommended_model"],
            sha256=expected_sha256,
            path_integration=managed_opencode_path_status(inspected["command"]),
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            phase="failed",
            message="OpenCode 自动安装没有完成",
            error=str(exc),
            next_action="检查网络后重试；也可以打开 OpenCode 官方发行页手动下载。",
        )


def start_opencode_install() -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("当前自动安装仅支持 Windows")
    with _jobs_lock:
        running = next((dict(job) for job in _jobs.values() if job.get("status") in {"pending", "running"}), None)
        if running:
            return running
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        _jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "phase": "queued",
            "percent": 0,
            "message": "安装任务已创建",
            "bytes_downloaded": 0,
            "bytes_total": 0,
            "created_at": now,
            "updated_at": now,
        }
        result = dict(_jobs[job_id])
    threading.Thread(target=_install_worker, args=(job_id,), daemon=True, name=f"opencode-install-{job_id[:8]}").start()
    return result


def get_opencode_install_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _activation_payload(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "operation_id": getattr(job, "operation_id", None),
        "status": job.status,
        "phase": job.phase,
        "percent": job.percent,
        "message": job.message or "",
        "error": job.error,
        "failure_kind": job.failure_kind,
        "next_action": job.next_action,
        "auth_mode": getattr(job, "auth_mode", None),
        "auth_status": getattr(job, "auth_status", None),
        "auth_prompt": getattr(job, "auth_prompt", None),
        "auth_url": getattr(job, "auth_url", None),
        "command": job.command,
        "version": job.version,
        "selected_model": job.selected_model,
        "preferred_model": job.preferred_model,
        "free_models": list(job.free_models_json or []),
        "download_url": job.download_url,
        "download_source": _download_source_label(job.download_url),
        "sha256": job.sha256,
        "bytes_downloaded": job.bytes_downloaded or 0,
        "bytes_total": job.bytes_total or 0,
        "estimated_seconds_remaining": job.estimated_seconds_remaining,
        "attempt_count": job.attempt_count or 0,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "path_integration": managed_opencode_path_status(job.command),
    }


def _update_activation(job_id: str, **changes: Any) -> dict[str, Any]:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        job = db.query(OpenCodeActivationJob).filter(OpenCodeActivationJob.id == job_id).first()
        if not job:
            raise RuntimeError("OpenCode 激活任务不存在")
        old_phase = job.phase
        old_status = job.status
        old_message = job.message
        old_model = job.selected_model
        old_percent = int(job.percent or 0)
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC).replace(tzinfo=None)
        if job.operation_id:
            from app.database.models import OperationRun
            from app.services.operation_runtime import update_operation

            operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
            if operation:
                operation.model_source = job.selected_model or operation.model_source
                lifecycle = {
                    "pending": "queued",
                    "running": "running",
                    "auth_required": "waiting_user",
                    "ready": "completed",
                    "failed": "failed",
                }.get(job.status, "running")
                determinate = bool(job.phase == "downloading" and job.bytes_total)
                meaningful = (
                    old_phase != job.phase
                    or old_status != job.status
                    or old_message != job.message
                    or old_model != job.selected_model
                    or abs(int(job.percent or 0) - old_percent) >= 1
                )
                update_operation(
                    db,
                    operation,
                    status=lifecycle,
                    phase=job.phase,
                    message=job.message,
                    event_type="activation_progress" if meaningful else None,
                    payload={
                        "phase": job.phase,
                        "selected_model": job.selected_model,
                        "previous_model": old_model,
                        "bytes_downloaded": job.bytes_downloaded,
                        "bytes_total": job.bytes_total,
                    } if meaningful else None,
                    progress_mode="determinate" if determinate else "indeterminate",
                    progress_current=int(job.bytes_downloaded or 0) if determinate else None,
                    progress_total=int(job.bytes_total or 0) if determinate else None,
                    failure_class=job.failure_kind,
                    next_action=job.next_action,
                    checkpoint=meaningful and int(job.percent or 0) > old_percent,
                )
        commit_session(db)
        db.refresh(job)
        return _activation_payload(job)


def get_opencode_activation_job(job_id: str) -> dict[str, Any] | None:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        job = db.query(OpenCodeActivationJob).filter(OpenCodeActivationJob.id == job_id).first()
        return _activation_payload(job) if job else None


def get_latest_opencode_activation_job(db: Any | None = None) -> dict[str, Any] | None:
    from app.database.models import OpenCodeActivationJob

    if db is not None:
        job = db.query(OpenCodeActivationJob).order_by(OpenCodeActivationJob.created_at.desc()).first()
        return _activation_payload(job) if job else None
    from app.database.session import SessionLocal

    with SessionLocal() as session:
        job = session.query(OpenCodeActivationJob).order_by(OpenCodeActivationJob.created_at.desc()).first()
        return _activation_payload(job) if job else None


def _safe_auth_text(value: object, credential: str = "") -> str:
    text = _ANSI_ESCAPE_RE.sub("", str(value or "")).replace("\x00", " ")
    if credential:
        text = text.replace(credential, "[redacted]")
    text = re.sub(
        r"(?i)((?:token|credential|secret|authorization)\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]",
        text,
    )
    return " ".join(text.split())[-600:]


def _spawn_auth_process(command: str) -> Any:
    try:
        from winpty import PtyProcess
    except ImportError as exc:  # pragma: no cover - packaged Windows runtime owns the dependency
        raise RuntimeError("司命缺少托管登录组件，请重新安装当前版本") from exc
    return PtyProcess.spawn(
        _subprocess_command(command, ["auth", "login", "--provider", "opencode"]),
        cwd=tempfile.gettempdir(),
    )


def _auth_list_has_credentials(command: str) -> bool:
    try:
        result = subprocess.run(
            _subprocess_command(command, ["auth", "list"]),
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = _safe_auth_text("\n".join([result.stdout or "", result.stderr or ""]))
    lowered = output.lower()
    return result.returncode == 0 and bool(output) and not any(
        token in lowered for token in ("no credentials", "0 credentials", "not logged in")
    )


def _authentication_worker(job_id: str, command: str, process: Any) -> None:
    opened_url = ""
    exit_code: int | None = None
    try:
        while process.isalive():
            try:
                chunk = process.read()
            except EOFError:
                break
            with _auth_sessions_lock:
                session = _auth_sessions.get(job_id)
                credential = session.credential if session else ""
            safe_chunk = _safe_auth_text(chunk, credential)
            if not safe_chunk:
                continue

            urls = _AUTH_URL_RE.findall(safe_chunk)
            auth_url = urls[-1].rstrip(".,);]") if urls else ""
            changes: dict[str, Any] = {
                "auth_prompt": safe_chunk,
                "auth_status": "running",
                "phase": "authenticating",
                "message": "正在等待 OpenCode 完成官方登录",
            }
            if auth_url:
                changes.update({"auth_mode": "browser", "auth_url": auth_url})
                if auth_url != opened_url:
                    import webbrowser

                    webbrowser.open(auth_url)
                    opened_url = auth_url
            if re.search(r"(?i)press\s+enter.{0,40}(browser|login|continue)", safe_chunk):
                process.write("\r")
            elif _AUTH_CREDENTIAL_PROMPT_RE.search(safe_chunk):
                changes.update({
                    "status": "auth_required",
                    "phase": "credential_required",
                    "auth_mode": "credential",
                    "auth_status": "credential_required",
                    "message": "OpenCode 正在等待一次性验证码或令牌",
                    "next_action": "在司命中输入官方页面给出的验证码或令牌；内容不会保存或写入日志。",
                })
            _update_activation(job_id, **changes)

        try:
            exit_code = process.wait()
        except Exception:
            exit_code = getattr(process, "exitstatus", None)

        if exit_code in (None, 0) and _auth_list_has_credentials(command):
            _update_activation(
                job_id,
                status="auth_required",
                phase="auth_required",
                auth_status="completed",
                percent=92,
                message="官方登录已完成，正在重新验证免费模型",
                error=None,
                failure_kind=None,
                next_action=None,
            )
            retry_opencode_activation(job_id)
            return

        _update_activation(
            job_id,
            status="auth_required",
            phase="auth_required",
            auth_status="failed",
            message="官方登录没有完成",
            failure_kind="authentication_required",
            next_action="重新开始登录；如果浏览器没有打开，可复制登录地址到浏览器。",
        )
    except Exception as exc:
        _update_activation(
            job_id,
            status="auth_required",
            phase="auth_required",
            auth_status="failed",
            message="托管登录过程已中断",
            error=_safe_auth_text(exc),
            failure_kind="authentication_required",
            next_action="点击重新登录；司命不会保存本次输入的凭据。",
        )
    finally:
        with _auth_sessions_lock:
            _auth_sessions.pop(job_id, None)


def start_opencode_authentication(job_id: str) -> dict[str, Any]:
    job = get_opencode_activation_job(job_id)
    if not job:
        raise RuntimeError("OpenCode 激活任务不存在")
    command = resolve_opencode_command(job.get("command"))
    if not command:
        raise RuntimeError("没有找到可运行的 OpenCode，请先重新检测或安装")

    with _auth_sessions_lock:
        existing = _auth_sessions.get(job_id)
        if existing and existing.process.isalive():
            return get_opencode_activation_job(job_id) or job
        process = _spawn_auth_process(command)
        _auth_sessions[job_id] = _ManagedAuthSession(process=process)

    payload = _update_activation(
        job_id,
        status="running",
        phase="authenticating",
        auth_mode="browser",
        auth_status="running",
        auth_prompt=None,
        auth_url=None,
        message="正在启动 OpenCode 官方登录",
        error=None,
        next_action="浏览器打开后完成登录；司命会自动继续验证。",
    )
    threading.Thread(
        target=_authentication_worker,
        args=(job_id, command, process),
        daemon=True,
        name=f"opencode-auth-{job_id[:8]}",
    ).start()
    return payload


def submit_opencode_auth_credential(job_id: str, credential: str) -> dict[str, Any]:
    value = str(credential or "").strip()
    if not value:
        raise RuntimeError("请输入官方登录页面提供的验证码或令牌")
    with _auth_sessions_lock:
        session = _auth_sessions.get(job_id)
        if not session or not session.process.isalive():
            raise RuntimeError("这次登录会话已经结束，请重新开始登录")
        session.credential = value
        session.process.write(value + "\r")
    return _update_activation(
        job_id,
        status="running",
        phase="authenticating",
        auth_status="submitted",
        auth_prompt="一次性凭据已提交，正在等待 OpenCode 验证",
        message="正在验证官方登录",
        next_action="请稍候，司命会自动继续。",
    )


def _install_for_activation(job_id: str) -> tuple[str, str, str]:
    root = managed_opencode_root()
    _update_activation(
        job_id,
        status="running",
        phase="checking_release",
        percent=2,
        message="正在选择经过校验的 OpenCode 官方稳定版",
    )
    version, asset = _latest_release_asset()
    expected_sha256 = str(asset["digest"]).removeprefix("sha256:")

    def on_event(event: str, details: dict[str, Any]) -> None:
        if event == "probing":
            _update_activation(
                job_id,
                status="running",
                phase="selecting_source",
                percent=3,
                message=f"正在测速 {details['source_count']} 条安全下载线路",
                sha256=expected_sha256,
            )
        elif event == "source_selected":
            source = details["source"]
            probe = details["probe"]
            measured = (
                f"，测速约 {probe.bytes_per_second / 1024 / 1024:.1f} MB/s"
                if probe.available and probe.bytes_per_second > 0
                else ""
            )
            resumed = "，继续已有进度" if details["resuming_bytes"] else ""
            _update_activation(
                job_id,
                status="running",
                phase="downloading",
                percent=5,
                message=f"已选择{source.label}{measured}{resumed}，正在下载 {version}",
                download_url=source.url,
                sha256=expected_sha256,
                bytes_downloaded=int(details["resuming_bytes"]),
                bytes_total=source.expected_size,
            )
        elif event == "progress":
            downloaded = int(details["downloaded"])
            total = int(details["total"])
            fraction = downloaded / total if total else 0
            _update_activation(
                job_id,
                percent=max(5, min(78, int(5 + fraction * 73))),
                bytes_downloaded=downloaded,
                bytes_total=total,
                estimated_seconds_remaining=details["estimated_seconds_remaining"],
            )
        elif event == "switching":
            _update_activation(
                job_id,
                phase="switching_source",
                message=f"当前线路速度过慢或连接失败，正在切换到{details['next_source'].label}",
            )

    archive_path, source, part_paths = _download_release_archive(
        root,
        asset,
        on_event=on_event,
    )
    command = managed_opencode_command()
    _update_activation(job_id, phase="verifying", percent=82, message="下载完成，正在校验并安装")
    _extract_opencode(archive_path, command, archive_format=source.archive_format)
    for part_path in part_paths:
        part_path.unlink(missing_ok=True)
    metadata = {
        "version": version,
        "asset": asset["name"],
        "sha256": expected_sha256,
        "source": source.url,
        "source_label": source.label,
        "source_digest": f"{source.digest_algorithm}:{source.expected_digest}",
        "command": str(command),
        "installed_at": datetime.now(UTC).isoformat(),
    }
    (root / "install.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(command), version, expected_sha256


def _activation_worker(job_id: str) -> None:
    try:
        current = get_opencode_activation_job(job_id)
        if not current:
            return
        _update_activation(
            job_id,
            status="running",
            phase="checking",
            percent=1,
            message="正在检查这台电脑",
            error=None,
            failure_kind=None,
            next_action=None,
        )
        command = resolve_opencode_command(current.get("command"))
        version = None
        sha256 = None
        inspected = inspect_opencode(command, refresh=True) if command else {"installed": False}
        if not inspected.get("installed"):
            command, version, sha256 = _install_for_activation(job_id)

        _update_activation(job_id, phase="verifying", percent=84, message="正在确认写作引擎可以运行")
        inspected = inspect_opencode(command, timeout=15, refresh=True)
        if not inspected.get("installed"):
            raise RuntimeError("写作引擎已经下载，但被系统或安全软件阻止运行")
        command = str(inspected["command"])
        version = str(inspected.get("version") or version or "")
        free_models = list(inspected.get("free_models") or [])
        if not free_models:
            raise RuntimeError("当前没有发现可免费使用的模型，请稍后重新检测")

        preferred = str(current.get("preferred_model") or "")
        ordered = sorted(
            free_models,
            key=lambda item: (
                str(item.get("id")) != preferred if preferred else not bool(item.get("recommended")),
                not bool(item.get("recommended")),
            ),
        )
        _update_activation(
            job_id,
            phase="discovering_models",
            percent=88,
            message="已找到当前可免费使用的模型",
            command=command,
            version=version,
            sha256=sha256 or current.get("sha256"),
            free_models_json=[{**item, "test_status": "untested"} for item in ordered],
        )

        probe = _probe_free_models(
            job_id=job_id,
            command=command,
            ordered=ordered,
            update_activation=_update_activation,
            test_model_call=_test_opencode_model,
        )
        failures = probe.failures
        model_results = probe.model_results
        if probe.selected_model:
            _save_activated_config(command, probe.selected_model)
            _update_activation(
                job_id,
                status="ready",
                phase="ready",
                percent=100,
                message="免费写作能力已经准备好",
                selected_model=probe.selected_model,
                free_models_json=deepcopy(model_results),
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            return

        authentication_failure = next((item for item in failures if item[1] == "authentication_required"), None)
        if authentication_failure:
            _save_activation_readiness_failure(authentication_failure[2])
            _update_activation(
                job_id,
                status="auth_required",
                phase="auth_required",
                percent=90,
                message="需要完成一次免费的官方登录",
                error=authentication_failure[2],
                failure_kind=authentication_failure[1],
                next_action="点击登录按钮，在官方页面完成登录后返回重试。",
            )
            return

        kinds = {item[1] for item in failures}
        if kinds == {"quota_or_rate_limit"}:
            _save_activation_readiness_failure(failures[-1][2])
            _update_activation(
                job_id,
                status="failed",
                phase="failed",
                percent=98,
                message="OpenCode 免费服务已限流",
                error=failures[-1][2],
                failure_kind="quota_or_rate_limit",
                next_action=(
                    f"司命已实际测试 {len(failures)} 个免费模型，第三方均返回 403/429 或额度限制；"
                    "这不是网络故障。可以等待额度恢复后重新检测，或先完成 OpenCode 官方登录，"
                    "再验证个人免费额度。"
                ),
                free_models_json=deepcopy(model_results),
            )
            return
        raise RuntimeError(
            "当前免费模型暂时都不可用，请稍后重试。"
            + (f" 技术详情：{failures[-1][2]}" if failures else "")
        )
    except Exception as exc:
        message = str(exc)
        latest = get_opencode_activation_job(job_id) or {}
        failure_context = (
            "download"
            if latest.get("phase") in {"checking_release", "downloading"}
            else None
        )
        kind = _activation_failure_kind(message, context=failure_context)
        _save_activation_readiness_failure(message, unavailable_fallback=True)
        _update_activation(
            job_id,
            status="failed",
            phase="failed",
            message="免费写作能力暂时没有准备完成",
            error=message,
            failure_kind=kind,
            next_action=(
                "请确认 Windows 日期和时间正确，并完成 Windows 更新后重试。司命会使用系统受信任证书，且不会关闭 HTTPS 校验。"
                if kind == "certificate_verification"
                else (
                    "OpenCode 官方下载服务返回 403/429 限流；这不是本机断网。下载进度已保留，请稍后继续下载。"
                    if kind == "download_rate_limit"
                    else (
                        "请检查网络后点击重试，司命会从上次下载进度继续。"
                        if kind == "network"
                        else "点击重试；如果仍然失败，可导出诊断信息反馈给项目维护者。"
                    )
                )
            ),
        )


def start_opencode_activation(*, preferred_model: str | None = None) -> dict[str, Any]:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    if os.name != "nt":
        raise RuntimeError("当前自动安装仅支持 Windows")
    with _activation_start_lock, SessionLocal() as db:
        active = (
            db.query(OpenCodeActivationJob)
            .filter(OpenCodeActivationJob.status.in_(["pending", "running", "auth_required"]))
            .order_by(OpenCodeActivationJob.created_at.desc())
            .first()
        )
        if active:
            return _activation_payload(active)
        job = OpenCodeActivationJob(
            status="pending",
            phase="checking",
            percent=0,
            message="免费体验任务已创建",
            preferred_model=preferred_model,
        )
        db.add(job)
        db.flush()
        from app.services.operation_runtime import ensure_operation

        operation = ensure_operation(
            db,
            source_kind="opencode_activation",
            source_id=job.id,
            title="准备免费写作 AI",
            status="queued",
            phase="checking",
            message="正在检查这台电脑",
            tool_mode="managed_opencode",
            resume_url="/getting-started",
            can_pause=False,
            can_cancel=False,
            can_retry=False,
            progress_mode="indeterminate",
        )
        job.operation_id = operation.id
        commit_session(db)
        db.refresh(job)
        payload = _activation_payload(job)
        job_id = job.id
    threading.Thread(
        target=_activation_worker,
        args=(job_id,),
        daemon=True,
        name=f"opencode-activate-{job_id[:8]}",
    ).start()
    return payload


def retry_opencode_activation(job_id: str) -> dict[str, Any]:
    from app.database.models import OpenCodeActivationJob
    from app.database.session import SessionLocal

    with _activation_start_lock, SessionLocal() as db:
        job = db.query(OpenCodeActivationJob).filter(OpenCodeActivationJob.id == job_id).first()
        if not job:
            raise RuntimeError("OpenCode 激活任务不存在")
        if job.status in {"pending", "running"}:
            return _activation_payload(job)
        job.status = "pending"
        job.phase = "checking"
        job.percent = 0
        job.error = None
        job.failure_kind = None
        job.next_action = None
        job.auth_mode = None
        job.auth_status = None
        job.auth_prompt = None
        job.auth_url = None
        job.attempt_count = (job.attempt_count or 0) + 1
        commit_session(db)
        payload = _activation_payload(job)
    threading.Thread(
        target=_activation_worker,
        args=(job_id,),
        daemon=True,
        name=f"opencode-retry-{job_id[:8]}",
    ).start()
    return payload


def open_opencode_authentication(job_id: str) -> dict[str, Any]:
    return start_opencode_authentication(job_id)


def resume_incomplete_opencode_activations() -> int:
    from app.database.models import APIConfig, OpenCodeActivationJob, OperationRun
    from app.database.session import SessionLocal
    from app.modules.model_runtime.infrastructure.readiness import READINESS_READY
    from app.services.operation_runtime import update_operation

    with SessionLocal() as db:
        jobs = db.query(OpenCodeActivationJob).filter(
            OpenCodeActivationJob.status.in_(["pending", "running", "auth_required"])
        ).all()
        if jobs and db.query(APIConfig.id).filter(
            APIConfig.readiness_status == READINESS_READY
        ).first():
            now = datetime.now(UTC).replace(tzinfo=None)
            for job in jobs:
                job.status = "ready"
                job.phase = "ready"
                job.percent = 100
                job.message = "系统中已有可用模型，已停止重复检测"
                job.error = None
                job.failure_kind = None
                job.next_action = None
                job.completed_at = job.completed_at or now
                if job.operation_id:
                    operation = db.query(OperationRun).filter(
                        OperationRun.id == job.operation_id
                    ).first()
                    if operation:
                        update_operation(
                            db,
                            operation,
                            status="completed",
                            phase="ready",
                            message=job.message,
                            event_type="activation_skipped",
                            result={"reason": "usable_model_already_available"},
                            outcome="completed",
                        )
            commit_session(db)
            return 0
        job_ids: list[str] = []
        for job in jobs:
            if job.phase in {"authenticating", "credential_required"} or job.auth_status in {
                "running", "submitted", "credential_required"
            }:
                job.status = "auth_required"
                job.phase = "auth_required"
                job.auth_status = "interrupted"
                job.message = "应用重新启动，请重新开始官方登录"
                job.next_action = "点击登录按钮重新开始；上次输入没有保存。"
            else:
                job.status = "pending"
                job.message = "应用重新启动，正在恢复免费体验任务"
                job_ids.append(job.id)
        commit_session(db)
    for job_id in job_ids:
        threading.Thread(
            target=_activation_worker,
            args=(job_id,),
            daemon=True,
            name=f"opencode-resume-{job_id[:8]}",
        ).start()
    return len(job_ids)
