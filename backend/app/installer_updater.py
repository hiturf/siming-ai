"""Installer-aware Windows update flow.

New installed builds prefer the Inno Setup package so a complete onedir runtime
can be replaced safely. Windows Authenticode verification is temporarily not
required because the project does not currently have a code-signing certificate;
SHA-256 verification remains mandatory for every downloaded update.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib import error as urllib_error

from . import updater as legacy
from .core.legacy_env import compatible_env_enabled, get_compatible_env
from .version import APP_VERSION, DEFAULT_UPDATE_MIRROR_REPO, DEFAULT_UPDATE_REPO

INSTALLER_NAME = "Siming-Setup.exe"
PORTABLE_NAME = legacy.EXE_NAME
INSTALL_MARKER = ".siming-installed"
INSTALLER_CHECKSUM_ASSET_NAMES = {
    "siming-setup.sha256",
    "siming-setup.exe.sha256",
}
DOWNLOAD_SOURCE_AUTO = "auto"
DOWNLOAD_SOURCE_GITHUB = "github"
DOWNLOAD_SOURCE_GITEE = "gitee"
DOWNLOAD_SOURCE_CUSTOM = "custom"
DOWNLOAD_SOURCE_LABELS = {
    DOWNLOAD_SOURCE_GITHUB: "GitHub",
    DOWNLOAD_SOURCE_GITEE: "Gitee 国内镜像",
    DOWNLOAD_SOURCE_CUSTOM: "自定义更新源",
}
# The project currently has no Windows Authenticode certificate. Keep the
# verification implementation available in updater.py, but do not block
# installer updates on it until a trusted signing certificate is configured.
WINDOWS_SIGNATURE_VERIFICATION_REQUIRED = False


def _valid_sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[a-f0-9]{64}", text) else ""


def _asset_sha256(asset: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    digest = str(asset.get("digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        value = _valid_sha256(digest.removeprefix("sha256:"))
        if value:
            return value

    checksum_asset = next(
        (
            candidate
            for candidate in assets
            if str(candidate.get("name") or "").lower() in INSTALLER_CHECKSUM_ASSET_NAMES
        ),
        None,
    )
    if checksum_asset and checksum_asset.get("browser_download_url"):
        try:
            text = legacy._request(
                str(checksum_asset["browser_download_url"]),
                timeout=6,
            ).decode("utf-8", errors="ignore")
        except Exception:
            return ""
        match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
        return match.group(1).lower() if match else ""
    return ""


def _manifest_from_release_payload(
    repo: str,
    release: dict[str, Any],
    *,
    download_source: str = DOWNLOAD_SOURCE_GITHUB,
) -> dict[str, Any] | None:
    tag = str(release.get("tag_name") or release.get("name") or "").strip()
    version = tag.removeprefix("v")
    raw_assets = release.get("assets")
    assets = (
        [asset for asset in raw_assets if isinstance(asset, dict)]
        if isinstance(raw_assets, list)
        else []
    )
    installer_asset = next(
        (
            asset
            for asset in assets
            if str(asset.get("name") or "").lower() == INSTALLER_NAME.lower()
        ),
        None,
    )
    if version and installer_asset and installer_asset.get("browser_download_url"):
        releases_url = (
            f"https://gitee.com/{repo}/releases"
            if download_source == DOWNLOAD_SOURCE_GITEE
            else f"https://github.com/{repo}/releases"
        )
        return {
            "version": version,
            "download_url": str(installer_asset["browser_download_url"]),
            "sha256": _asset_sha256(installer_asset, assets),
            "source": release.get("html_url") or releases_url,
            "releases_url": releases_url,
            "download_source": download_source,
            "download_source_label": DOWNLOAD_SOURCE_LABELS[download_source],
            "asset_name": INSTALLER_NAME,
            "install_mode": "installer",
        }

    portable = legacy._manifest_from_release_payload(repo, release)
    if not portable:
        return None
    return {
        **portable,
        "source": (
            str(portable.get("source") or "")
            if download_source == DOWNLOAD_SOURCE_GITHUB
            else f"https://gitee.com/{repo}/releases"
        ),
        "releases_url": (
            f"https://gitee.com/{repo}/releases"
            if download_source == DOWNLOAD_SOURCE_GITEE
            else f"https://github.com/{repo}/releases"
        ),
        "download_source": download_source,
        "download_source_label": DOWNLOAD_SOURCE_LABELS[download_source],
        "asset_name": PORTABLE_NAME,
        "install_mode": "portable",
    }


def _release_has_update_asset(release: dict[str, Any]) -> bool:
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    supported = {INSTALLER_NAME.lower(), PORTABLE_NAME.lower()}
    return any(
        isinstance(asset, dict) and str(asset.get("name") or "").lower() in supported
        for asset in assets
    )


def _manifest_from_github_release(
    repo: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    selected_channel = legacy.resolve_update_channel(channel)
    if selected_channel == "stable":
        release = legacy._request_json(f"https://api.github.com/repos/{repo}/releases/latest")
        return _manifest_from_release_payload(repo, release) if isinstance(release, dict) else None

    releases = legacy._request_json(f"https://api.github.com/repos/{repo}/releases?per_page=30")
    if not isinstance(releases, list):
        return None
    eligible = [
        release
        for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and _release_has_update_asset(release)
    ]
    if not eligible:
        return None
    latest = eligible[0]
    for candidate in eligible[1:]:
        candidate_version = str(candidate.get("tag_name") or candidate.get("name") or "")
        latest_version = str(latest.get("tag_name") or latest.get("name") or "")
        if legacy.is_newer_version(candidate_version, latest_version):
            latest = candidate
    return _manifest_from_release_payload(repo, latest)


def _manifest_from_gitee_release(
    repo: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    selected_channel = legacy.resolve_update_channel(channel)
    api_base = f"https://gitee.com/api/v5/repos/{repo}/releases"
    if selected_channel == "stable":
        release = legacy._request_json(f"{api_base}/latest")
        return (
            _manifest_from_release_payload(
                repo,
                release,
                download_source=DOWNLOAD_SOURCE_GITEE,
            )
            if isinstance(release, dict)
            else None
        )

    releases = legacy._request_json(f"{api_base}?per_page=30")
    if not isinstance(releases, list):
        return None
    eligible = [
        release
        for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and _release_has_update_asset(release)
    ]
    if not eligible:
        return None
    latest = eligible[0]
    for candidate in eligible[1:]:
        candidate_version = str(candidate.get("tag_name") or candidate.get("name") or "")
        latest_version = str(latest.get("tag_name") or latest.get("name") or "")
        if legacy.is_newer_version(candidate_version, latest_version):
            latest = candidate
    return _manifest_from_release_payload(
        repo,
        latest,
        download_source=DOWNLOAD_SOURCE_GITEE,
    )


def _load_release_manifests(
    github_repo: str,
    gitee_repo: str,
    channel: str,
) -> list[dict[str, Any]]:
    """Load both public release sources without making one block the other."""
    loaders = [
        lambda: _manifest_from_github_release(github_repo, channel),
        lambda: _manifest_from_gitee_release(gitee_repo, channel),
    ]
    manifests: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(loaders)) as executor:
        futures = [executor.submit(loader) for loader in loaders]
        for future in futures:
            try:
                manifest = future.result()
            except (
                OSError,
                urllib_error.URLError,
                urllib_error.HTTPError,
                json.JSONDecodeError,
            ):
                continue
            if manifest and manifest.get("download_url"):
                manifests.append(manifest)
    return manifests


def _download_source_payload(manifest: dict[str, Any]) -> dict[str, str]:
    key = str(manifest.get("download_source") or DOWNLOAD_SOURCE_CUSTOM)
    return {
        "key": key,
        "label": str(
            manifest.get("download_source_label") or DOWNLOAD_SOURCE_LABELS.get(key) or key
        ),
        "download_url": str(manifest.get("download_url") or ""),
        "releases_url": str(manifest.get("releases_url") or manifest.get("source") or ""),
    }


def _merge_release_manifests(
    manifests: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not manifests:
        return None
    latest_version = str(manifests[0].get("version") or "")
    for candidate in manifests[1:]:
        version = str(candidate.get("version") or "")
        if legacy.is_newer_version(version, latest_version):
            latest_version = version

    matching = [
        manifest
        for manifest in manifests
        if not legacy.is_newer_version(
            str(manifest.get("version") or ""),
            latest_version,
        )
        and not legacy.is_newer_version(
            latest_version,
            str(manifest.get("version") or ""),
        )
    ]
    matching.sort(
        key=lambda manifest: 0 if manifest.get("download_source") == DOWNLOAD_SOURCE_GITHUB else 1
    )
    primary = dict(matching[0])
    expected_sha256 = next(
        (
            checksum
            for checksum in (_valid_sha256(manifest.get("sha256")) for manifest in matching)
            if checksum
        ),
        "",
    )
    if expected_sha256:
        primary["sha256"] = expected_sha256

    compatible_sources = []
    for manifest in matching:
        source_sha256 = _valid_sha256(manifest.get("sha256"))
        if expected_sha256 and source_sha256 and source_sha256 != expected_sha256:
            continue
        compatible_sources.append(_download_source_payload(manifest))
    compatible_sources.sort(key=lambda item: 0 if item["key"] == DOWNLOAD_SOURCE_GITHUB else 1)
    primary["download_sources"] = compatible_sources
    return primary


def _manifest_from_url(url: str) -> dict[str, Any] | None:
    data = legacy._request_json(url)
    if not isinstance(data, dict):
        return None
    version = str(data.get("version") or data.get("tag_name") or "").strip().removeprefix("v")
    download_url = str(data.get("download_url") or data.get("url") or "").strip()
    if not version or not download_url:
        return None
    asset_name = str(data.get("asset_name") or Path(download_url).name or "").strip()
    install_mode = str(data.get("install_mode") or "").strip().lower()
    if install_mode not in {"installer", "portable"}:
        install_mode = "installer" if asset_name.lower() == INSTALLER_NAME.lower() else "portable"
    return {
        "version": version,
        "download_url": download_url,
        "sha256": _valid_sha256(data.get("sha256")),
        "source": url,
        "releases_url": str(data.get("releases_url") or url),
        "download_source": DOWNLOAD_SOURCE_CUSTOM,
        "download_source_label": str(
            data.get("download_source_label") or DOWNLOAD_SOURCE_LABELS[DOWNLOAD_SOURCE_CUSTOM]
        ),
        "asset_name": asset_name
        or (INSTALLER_NAME if install_mode == "installer" else PORTABLE_NAME),
        "install_mode": install_mode,
    }


def _running_install_root() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    current = Path(sys.executable).resolve()
    if current.name.lower() != PORTABLE_NAME.lower():
        return None
    return current.parent if (current.parent / INSTALL_MARKER).is_file() else None


def _portable_migration_needed(manifest: dict[str, Any]) -> bool:
    if str(manifest.get("install_mode") or "") != "installer":
        return False
    if not getattr(sys, "frozen", False):
        return False
    current = Path(sys.executable).resolve()
    if current.name.lower() != PORTABLE_NAME.lower():
        return False
    if (current.parent / INSTALL_MARKER).is_file():
        return False
    latest = str(manifest.get("version") or "").strip().removeprefix("v")
    current_version = str(APP_VERSION).strip().removeprefix("v")
    return latest == current_version


def find_latest_update(channel: str | None = None) -> dict[str, Any] | None:
    """Return the newest release available from GitHub or the Gitee mirror."""
    if compatible_env_enabled("SIMING_DISABLE_UPDATE"):
        return None
    manifest_url = get_compatible_env("SIMING_UPDATE_MANIFEST_URL").strip()
    repo = get_compatible_env(
        "SIMING_UPDATE_REPO",
        default=DEFAULT_UPDATE_REPO,
    ).strip()
    mirror_repo = get_compatible_env(
        "SIMING_UPDATE_MIRROR_REPO",
        default=DEFAULT_UPDATE_MIRROR_REPO,
    ).strip()
    selected_channel = legacy.resolve_update_channel(channel)
    try:
        manifest = (
            _manifest_from_url(manifest_url)
            if manifest_url
            else _merge_release_manifests(
                _load_release_manifests(repo, mirror_repo, selected_channel)
            )
        )
    except (
        OSError,
        urllib_error.URLError,
        urllib_error.HTTPError,
        json.JSONDecodeError,
    ):
        return None
    if not manifest or not manifest.get("download_url"):
        return None
    if not manifest.get("download_sources"):
        manifest["download_sources"] = [_download_source_payload(manifest)]
    manifest["channel"] = selected_channel
    manifest["migration"] = _portable_migration_needed(manifest)
    if legacy.is_newer_version(str(manifest.get("version") or "")):
        return manifest
    return manifest if manifest["migration"] else None


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    download_sources = manifest.get("download_sources")
    public_sources = (
        [
            {
                "key": str(source.get("key") or ""),
                "label": str(source.get("label") or ""),
                "download_url": str(source.get("download_url") or ""),
                "releases_url": str(source.get("releases_url") or ""),
            }
            for source in download_sources
            if isinstance(source, dict) and source.get("download_url")
        ]
        if isinstance(download_sources, list)
        else []
    )
    return {
        "version": str(manifest.get("version") or ""),
        "channel": str(manifest.get("channel") or ""),
        "source": str(manifest.get("source") or ""),
        "download_url": str(manifest.get("download_url") or ""),
        "asset_name": str(manifest.get("asset_name") or ""),
        "install_mode": str(manifest.get("install_mode") or ""),
        "migration": bool(manifest.get("migration")),
        "sha256_available": bool(_valid_sha256(manifest.get("sha256"))),
        "download_sources": public_sources,
    }


def _manual_download_pages() -> list[dict[str, str]]:
    github_repo = get_compatible_env(
        "SIMING_UPDATE_REPO",
        default=DEFAULT_UPDATE_REPO,
    ).strip()
    gitee_repo = get_compatible_env(
        "SIMING_UPDATE_MIRROR_REPO",
        default=DEFAULT_UPDATE_MIRROR_REPO,
    ).strip()
    return [
        {
            "key": DOWNLOAD_SOURCE_GITHUB,
            "label": "GitHub 全部版本",
            "url": f"https://github.com/{github_repo}/releases",
            "description": "官方完整发布记录与历史版本",
        },
        {
            "key": DOWNLOAD_SOURCE_GITEE,
            "label": "Gitee 镜像下载",
            "url": f"https://gitee.com/{gitee_repo}/releases",
            "description": "大陆网络备用，可手动选择镜像中保留的版本",
        },
    ]


def _verify_signature_if_required(path: Path) -> dict[str, Any] | None:
    if not WINDOWS_SIGNATURE_VERIFICATION_REQUIRED:
        return None
    return legacy._require_valid_signature(path)


def _validate_staged_update(app_home: Path) -> dict[str, Any]:
    """Revalidate a staged installer using SHA-256; signature is optional for now."""
    staged = legacy._read_staged_update(app_home)
    if not staged:
        raise RuntimeError("No downloaded update is waiting to be installed.")
    update_path = Path(str(staged.get("path") or "")).expanduser()
    expected_sha256 = str(staged.get("sha256") or "").strip().lower()
    if not update_path.is_file() or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise RuntimeError("The downloaded update is incomplete. Please download it again.")
    actual_sha256 = legacy._sha256_file(update_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            "The downloaded update checksum no longer matches the verified SHA-256 value."
        )
    staged["signature"] = _verify_signature_if_required(update_path)
    staged["sha256"] = actual_sha256
    return staged


def get_update_status(
    app_home: Path,
    channel: str | None = None,
) -> dict[str, Any]:
    selected_channel = legacy.resolve_update_channel(channel)
    manifest = find_latest_update(selected_channel)
    staged = legacy._read_staged_update(app_home)
    staged_payload = None
    if staged:
        staged_payload = {
            "version": str(staged.get("version") or ""),
            "sha256": str(staged.get("sha256") or ""),
            "signature": staged.get("signature")
            if isinstance(staged.get("signature"), dict)
            else None,
            "install_mode": str(staged.get("install_mode") or "portable"),
            "migration": bool(staged.get("migration")),
            "download_source": str(staged.get("download_source") or ""),
            "download_source_label": str(staged.get("download_source_label") or ""),
            "ready_to_install": False,
        }
        try:
            _validate_staged_update(app_home)
            staged_payload["ready_to_install"] = True
        except Exception as exc:
            staged_payload["error"] = str(exc)
    return {
        "current_version": APP_VERSION,
        "update_channel": selected_channel,
        "update_available": bool(manifest),
        "update": _public_manifest(manifest) if manifest else None,
        "staged_update": staged_payload,
        "automatic_updates": False,
        "installed_layout": _running_install_root() is not None,
        "signature_verification_required": WINDOWS_SIGNATURE_VERIFICATION_REQUIRED,
        "manual_download_pages": _manual_download_pages(),
    }


def _download_candidates(
    manifest: dict[str, Any],
    requested_source: str,
) -> list[dict[str, str]]:
    raw_sources = manifest.get("download_sources")
    sources = (
        [source for source in raw_sources if isinstance(source, dict)]
        if isinstance(raw_sources, list)
        else []
    )
    if not sources:
        sources = [_download_source_payload(manifest)]

    if requested_source != DOWNLOAD_SOURCE_AUTO:
        selected = [source for source in sources if source.get("key") == requested_source]
        if selected:
            return selected
        label = DOWNLOAD_SOURCE_LABELS.get(requested_source, requested_source)
        raise RuntimeError(f"版本 {manifest.get('version')} 暂未同步到 {label}。")

    priority = {
        DOWNLOAD_SOURCE_GITHUB: 0,
        DOWNLOAD_SOURCE_GITEE: 1,
        DOWNLOAD_SOURCE_CUSTOM: 2,
    }
    return sorted(sources, key=lambda source: priority.get(str(source.get("key")), 3))


def download_and_stage_update(
    app_home: Path,
    channel: str | None = None,
    download_source: str = DOWNLOAD_SOURCE_AUTO,
) -> dict[str, Any]:
    """Download from the selected source and require a matching release SHA-256."""
    selected_channel = legacy.resolve_update_channel(channel)
    manifest = find_latest_update(selected_channel)
    if not manifest:
        return get_update_status(app_home, selected_channel)
    expected_sha256 = legacy._expected_sha256(manifest)
    candidates = _download_candidates(manifest, download_source)
    selected_source = candidates[0]
    updates_dir = legacy._updates_dir(app_home)
    updates_dir.mkdir(parents=True, exist_ok=True)
    version = str(manifest["version"]).strip().removeprefix("v")
    install_mode = str(manifest.get("install_mode") or "portable")
    target_name = (
        f"Siming-Setup-{version}.exe" if install_mode == "installer" else f"Siming-{version}.exe"
    )
    target = updates_dir / target_name
    partial = target.with_name(target.name + ".part")
    try:
        if target.exists() and legacy._sha256_file(target) != expected_sha256:
            target.unlink(missing_ok=True)
        if not target.exists():
            download_errors: list[str] = []
            for candidate in candidates:
                partial.unlink(missing_ok=True)
                try:
                    legacy._download_to_file(
                        str(candidate.get("download_url") or ""),
                        partial,
                    )
                except Exception as exc:
                    partial.unlink(missing_ok=True)
                    download_errors.append(
                        f"{candidate.get('label') or candidate.get('key')}: {exc}"
                    )
                    continue
                actual_sha256 = legacy._sha256_file(partial)
                if actual_sha256 != expected_sha256:
                    raise RuntimeError(
                        "Downloaded update checksum does not match the release manifest."
                    )
                partial.replace(target)
                selected_source = candidate
                break
            else:
                detail = "；".join(download_errors) or "没有可用下载源"
                raise RuntimeError(f"无法下载更新。{detail}")
        actual_sha256 = legacy._sha256_file(target)
        if actual_sha256 != expected_sha256:
            raise RuntimeError("Downloaded update checksum does not match the release manifest.")
        signature = _verify_signature_if_required(target)
    except Exception:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    staged = {
        "version": version,
        "path": str(target.resolve()),
        "sha256": actual_sha256,
        "source": str(selected_source.get("releases_url") or manifest.get("source") or ""),
        "download_source": str(selected_source.get("key") or ""),
        "download_source_label": str(selected_source.get("label") or ""),
        "asset_name": str(manifest.get("asset_name") or ""),
        "install_mode": install_mode,
        "migration": bool(manifest.get("migration")),
        "signature": signature,
    }
    legacy._write_staged_update(app_home, staged)
    result = get_update_status(app_home, selected_channel)
    result["downloaded"] = True
    result["staged_update"] = {
        "version": version,
        "sha256": actual_sha256,
        "signature": signature,
        "install_mode": install_mode,
        "migration": bool(manifest.get("migration")),
        "download_source": str(selected_source.get("key") or ""),
        "download_source_label": str(selected_source.get("label") or ""),
        "ready_to_install": True,
    }
    return result


def schedule_staged_update_install(app_home: Path) -> dict[str, Any]:
    staged = _validate_staged_update(app_home)
    if str(staged.get("install_mode") or "portable") != "installer":
        return legacy.schedule_staged_update_install(app_home)

    current_exe = legacy._current_packaged_executable()
    installer = Path(str(staged["path"])).resolve()
    installed_layout = (current_exe.parent / INSTALL_MARKER).is_file()
    command = [str(installer)]
    if installed_layout:
        command.extend(
            [
                "/SP-",
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/DIR={current_exe.parent}",
            ]
        )
    else:
        # First migration from the legacy one-file build remains interactive so
        # the user can choose the install directory and desktop shortcut.
        command.append("/SP-")

    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(installer.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "version": str(staged.get("version") or ""),
        "signature": staged.get("signature"),
        "install_mode": "installer",
        "migration": not installed_layout,
        "restart_scheduled": True,
    }


def apply_update_if_available(app_home: Path) -> bool:
    return legacy.apply_update_if_available(app_home)


__all__ = [
    "apply_update_if_available",
    "download_and_stage_update",
    "find_latest_update",
    "get_update_status",
    "schedule_staged_update_install",
]
