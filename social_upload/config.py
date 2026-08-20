from __future__ import annotations

import json
import os
import re
from pathlib import Path

from aurexvideo_paths import CONFIG_ROOT, RESOURCE_ROOT

REPO_ROOT = RESOURCE_ROOT
# Native startup may rebind path roots as strings; normalize them once here.
SOCIAL_UPLOAD_CONFIG = Path(CONFIG_ROOT).expanduser().resolve() / "social-upload.json"
SOCIAL_UPLOAD_EXAMPLE = Path(REPO_ROOT).expanduser().resolve() / "config" / "social-upload.example.json"
SOCIAL_ROUTE_ID_KEYS = {"facebook": "page_id", "youtube": "channel_id"}
SOCIAL_ROUTE_PLATFORMS = ("youtube", "facebook", "instagram", "tiktok", "threads", "binance")
SOCIAL_BRAND_ROUTES_VERSION = 1


def read_social_config() -> dict:
    if not SOCIAL_UPLOAD_CONFIG.exists():
        return {}
    try:
        data = json.loads(SOCIAL_UPLOAD_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Social upload config is invalid JSON: {SOCIAL_UPLOAD_CONFIG}") from exc
    return data if isinstance(data, dict) else {}


def write_social_config(data: dict) -> None:
    SOCIAL_UPLOAD_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    SOCIAL_UPLOAD_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(SOCIAL_UPLOAD_CONFIG, 0o600)
    except OSError:
        pass


def social_config_hint() -> str:
    return "YouTube chưa cấu hình. Bấm Thêm channel để nhập OAuth Client ID và Client Secret."


def social_brand_routes(config: dict | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """Return only non-secret brand destinations from the native config."""
    config = read_social_config() if config is None else config
    raw_routes = config.get("brand_routes") if isinstance(config, dict) else None
    if not isinstance(raw_routes, dict):
        return {}

    result: dict[str, dict[str, dict[str, str]]] = {}
    for raw_brand, raw_platforms in raw_routes.items():
        brand = str(raw_brand or "").strip().casefold()
        if not brand or not isinstance(raw_platforms, dict):
            continue
        for platform, identity_key in SOCIAL_ROUTE_ID_KEYS.items():
            raw_route = raw_platforms.get(platform)
            if not isinstance(raw_route, dict):
                continue
            identity = str(raw_route.get(identity_key) or "").strip()
            if not identity:
                continue
            route = {identity_key: identity}
            name = str(raw_route.get("name") or "").strip()
            if name:
                route["name"] = name
            result.setdefault(brand, {})[platform] = route
    return result


def social_brand_route_records(config: dict | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """Return sanitized per-brand destinations for every supported platform.

    YouTube/Facebook routes use their native identity keys. The other
    integrations currently have one configured account per platform, so their
    route is represented by a stable ``connection_id`` and can be upgraded to
    multiple accounts without changing the upload UI contract.
    """
    config = read_social_config() if config is None else config
    raw_routes = config.get("brand_routes") if isinstance(config, dict) else None
    if not isinstance(raw_routes, dict):
        return {}

    result: dict[str, dict[str, dict[str, str]]] = {}
    for raw_brand, raw_platforms in raw_routes.items():
        brand = str(raw_brand or "").strip().casefold()
        if not brand or not isinstance(raw_platforms, dict):
            continue
        for platform in SOCIAL_ROUTE_PLATFORMS:
            raw_route = raw_platforms.get(platform)
            if not isinstance(raw_route, dict):
                continue
            identity_key = SOCIAL_ROUTE_ID_KEYS.get(platform)
            raw_identity = (
                raw_route.get(identity_key)
                if identity_key
                else raw_route.get("connection_id") or raw_route.get("account_id") or raw_route.get("id")
            )
            identity = str(raw_identity or "").strip()
            if not identity:
                continue
            route = {"connection_id": identity}
            if identity_key:
                route[identity_key] = identity
            name = str(raw_route.get("name") or raw_route.get("display_name") or "").strip()
            if name:
                route["name"] = name
            result.setdefault(brand, {})[platform] = route
    return result


def save_social_brand_route(
    brand: str,
    platform: str,
    connection_id: str,
    *,
    name: str = "",
    config: dict | None = None,
) -> dict[str, dict[str, str]]:
    """Persist one non-secret destination mapping for a Brand."""
    brand = str(brand or "").strip().casefold()
    platform = str(platform or "").strip().casefold()
    connection_id = str(connection_id or "").strip()
    if not brand:
        raise ValueError("Brand không được để trống.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
        raise ValueError("Brand chỉ được dùng chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.")
    if platform not in SOCIAL_ROUTE_PLATFORMS:
        raise ValueError(f"Unsupported social platform: {platform or '<empty>'}.")
    if not connection_id or len(connection_id) > 256:
        raise ValueError("Social connection không hợp lệ.")

    config = read_social_config() if config is None else config
    routes = config.get("brand_routes")
    if not isinstance(routes, dict):
        routes = {}
    brand_routes = routes.get(brand)
    if not isinstance(brand_routes, dict):
        brand_routes = {}

    identity_key = SOCIAL_ROUTE_ID_KEYS.get(platform)
    route: dict[str, str] = {identity_key or "connection_id": connection_id}
    if identity_key:
        route["connection_id"] = connection_id
    clean_name = str(name or "").strip()
    if clean_name:
        route["name"] = clean_name[:160]
    brand_routes[platform] = route
    routes[brand] = brand_routes
    config["brand_routes"] = routes
    try:
        config["brand_routes_version"] = int(config.get("brand_routes_version") or 0) + 1
    except (TypeError, ValueError):
        config["brand_routes_version"] = SOCIAL_BRAND_ROUTES_VERSION + 1
    write_social_config(config)
    return social_brand_route_records(config).get(brand, {})


def social_brand_routes_version(config: dict | None = None) -> int:
    config = read_social_config() if config is None else config
    try:
        version = int(config.get("brand_routes_version") or 0) if isinstance(config, dict) else 0
    except (TypeError, ValueError):
        version = 0
    return version or SOCIAL_BRAND_ROUTES_VERSION


def social_brand_route(config: dict, brand: str, platform: str) -> dict[str, str]:
    """Resolve one explicit brand destination without falling back to active."""
    platform = str(platform or "").strip().casefold()
    identity_key = SOCIAL_ROUTE_ID_KEYS.get(platform)
    if not identity_key:
        raise ValueError(f"Unsupported social platform: {platform or '<empty>'}.")
    brand = str(brand or "").strip().casefold()
    route = social_brand_routes(config).get(brand, {}).get(platform)
    if not route or not route.get(identity_key):
        raise ValueError(f"Social route chưa cấu hình cho brand {brand or '<empty>'} trên {platform}.")
    return route
