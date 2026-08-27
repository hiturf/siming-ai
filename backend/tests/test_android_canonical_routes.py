"""The Android allowlist must expose existing PC routes, never a second dialect."""

from app.bootstrap.app_factory import create_app
from app.bootstrap.http_security import GatewayAuthenticationMiddleware, REMOTE_ANDROID_AUTHORING_PATHS


def test_android_authoring_allowlist_is_a_subset_of_pc_openapi():
    paths = create_app(run_startup=False).openapi()["paths"]

    for path, methods in REMOTE_ANDROID_AUTHORING_PATHS.items():
        assert path in paths, path
        published = set(paths[path])
        for method in methods - {"HEAD"}:
            assert method.lower() in published, f"{method} {path}"


def test_android_assistant_lifecycle_routes_are_explicitly_allowlisted():
    concrete_paths = (
        "/api/v1/projects/project-1/chapter-drafts/pending",
        "/api/v1/projects/project-1/ai/assistant/conversations",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1",
        "/api/v1/projects/project-1/ai/assistant/runs",
        "/api/v1/projects/project-1/ai/assistant/runs/run-1",
        "/api/v1/projects/project-1/ai/assistant/runs/run-1/cancel",
    )
    assert all(
        GatewayAuthenticationMiddleware.is_remote_android_authoring_path(path)
        for path in concrete_paths
    )
