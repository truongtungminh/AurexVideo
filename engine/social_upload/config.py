from __future__ import annotations

import json
import os
import re
import hashlib
from pathlib import Path

from aurexvideo_paths import CONFIG_ROOT, RESOURCE_ROOT

REPO_ROOT = RESOURCE_ROOT
# Native startup may rebind path roots as strings; normalize them once here.
SOCIAL_UPLOAD_CONFIG = Path(CONFIG_ROOT).expanduser().resolve() / "social-upload.json"
SOCIAL_UPLOAD_EXAMPLE = Path(REPO_ROOT).expanduser().resolve() / "config" / "social-upload.example.json"
SOCIAL_ROUTE_ID_KEYS = {"facebook": "page_id", "youtube": "channel_id", "shopee": "connection_id"}
SOCIAL_ROUTE_PLATFORMS = ("youtube", "facebook", "instagram", "tiktok", "threads", "binance", "shopee")
SOCIAL_BRAND_ROUTES_VERSION = 1
SOCIAL_BRAND_CONNECTIONS_VERSION = 1
LEGACY_SOCIAL_BRAND = "popsy"
BRAND_ALIASES = {
    "tintucbitcoin": "july",
}
SOCIAL_CONNECTION_CONFIG_KEYS = {
    "instagram": "instagram",
    "tiktok": "zernio",
    "threads": "threads",
    "shopee": "shopee",
}
SOCIAL_CONNECTION_ENV_KEYS = {
    "instagram": {
        "ig_user_id": "INSTAGRAM_IG_USER_ID",
        "access_token": "INSTAGRAM_ACCESS_TOKEN",
    },
    "tiktok": {
        "account_id": "ZERNIO_TIKTOK_ACCOUNT_ID",
        "api_key": "ZERNIO_API_KEY",
        "base_url": "ZERNIO_BASE_URL",
    },
    "threads": {
        "threads_user_id": "THREADS_USER_ID",
        "access_token": "THREADS_ACCESS_TOKEN",
    },
    "shopee": {
        "app_id": "SHOPEE_AFFILIATE_APP_ID",
        "secret": "SHOPEE_AFFILIATE_SECRET",
        "api_base_url": "SHOPEE_AFFILIATE_API_BASE_URL",
    },
}
SOCIAL_CONNECTION_PUBLIC_ID_KEYS = {
    "instagram": "ig_user_id",
    "tiktok": "account_id",
    "threads": "threads_user_id",
    "shopee": "app_id",
}
SOCIAL_CONNECTION_REQUIRED_KEYS = {
    "instagram": ("ig_user_id", "access_token"),
    "tiktok": ("account_id", "api_key"),
    "threads": ("threads_user_id", "access_token"),
    "shopee": ("app_id", "secret"),
}
SOCIAL_CONNECTION_COPY_KEYS = {
    "instagram": (
        "ig_user_id", "access_token", "api_mode", "graph_version", "display_name",
        "poll_attempts", "poll_interval_seconds",
    ),
    "tiktok": ("account_id", "api_key", "base_url", "display_name"),
    "threads": (
        "threads_user_id", "access_token", "graph_version", "display_name",
        "poll_attempts", "poll_interval_seconds",
    ),
    "shopee": ("app_id", "secret", "api_base_url", "display_name"),
}
SOCIAL_CONNECTION_DEFAULT_NAMES = {
    "instagram": "Popsy Instagram",
    "tiktok": "Popsy TikTok",
    "threads": "Popsy Threads",
    "shopee": "Shopee Affiliate",
}


def canonical_brand(value: object) -> str:
    """Return the canonical Brand id while keeping old ids backwards-compatible."""
    brand = str(value or "").strip().casefold()
    seen = set()
    while brand in BRAND_ALIASES and brand not in seen:
        seen.add(brand)
        brand = BRAND_ALIASES[brand]
    return brand


def migrate_brand_aliases(config: dict) -> tuple[dict, bool]:
    """Move legacy Brand aliases into their canonical route/connection owner."""
    if not isinstance(config, dict):
        return {}, False
    changed = False
    raw_routes = config.get("brand_routes")
    if isinstance(raw_routes, dict):
        normalized_routes: dict[str, dict] = {}
        for raw_brand, raw_platforms in raw_routes.items():
            brand = canonical_brand(raw_brand)
            if not brand or not isinstance(raw_platforms, dict):
                continue
            target = normalized_routes.setdefault(brand, {})
            if brand != str(raw_brand or "").strip().casefold():
                changed = True
            for platform, route in raw_platforms.items():
                if not isinstance(route, dict):
                    continue
                if platform not in target:
                    target[platform] = dict(route)
                else:
                    for key, value in route.items():
                        target[platform].setdefault(key, value)
        if normalized_routes != raw_routes:
            config["brand_routes"] = normalized_routes
            changed = True

    for section_key in SOCIAL_CONNECTION_CONFIG_KEYS.values():
        section = config.get(section_key)
        if not isinstance(section, dict):
            continue
        connections = section.get("connections")
        if not isinstance(connections, dict):
            continue
        for connection in connections.values():
            if not isinstance(connection, dict):
                continue
            owner = str(connection.get("brand") or "").strip().casefold()
            normalized_owner = canonical_brand(owner)
            if owner and owner != normalized_owner:
                connection["brand"] = normalized_owner
                changed = True

    if changed:
        try:
            config["brand_routes_version"] = int(config.get("brand_routes_version") or 0) + 1
        except (TypeError, ValueError):
            config["brand_routes_version"] = SOCIAL_BRAND_ROUTES_VERSION + 1
        config["brand_connections_version"] = SOCIAL_BRAND_CONNECTIONS_VERSION
    return config, changed


def read_social_config() -> dict:
    if not SOCIAL_UPLOAD_CONFIG.exists():
        return {}
    try:
        data = json.loads(SOCIAL_UPLOAD_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Social upload config is invalid JSON: {SOCIAL_UPLOAD_CONFIG}") from exc
    data = data if isinstance(data, dict) else {}
    data, aliases_changed = migrate_brand_aliases(data)
    data, connections_changed = migrate_legacy_social_connections(data)
    if aliases_changed or connections_changed:
        write_social_config(data)
    return data


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
        brand = canonical_brand(raw_brand)
        if not brand or not isinstance(raw_platforms, dict):
            continue
        for platform in SOCIAL_ROUTE_PLATFORMS:
            raw_route = raw_platforms.get(platform)
            if not isinstance(raw_route, dict):
                continue
            identity_key = SOCIAL_ROUTE_ID_KEYS.get(platform)
            identity = str(
                raw_route.get(identity_key)
                if identity_key
                else raw_route.get("connection_id") or raw_route.get("account_id") or raw_route.get("id")
                or ""
            ).strip()
            if not identity:
                continue
            route = {identity_key or "connection_id": identity}
            name = str(raw_route.get("name") or "").strip()
            if name:
                route["name"] = name
            result.setdefault(brand, {}).setdefault(platform, route)
    return result


def social_brand_route_records(config: dict | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """Return sanitized per-brand destinations for every supported platform.

    YouTube/Facebook routes use their native identity keys. The other
    integrations currently have one configured account per platform, so their
    route is represented by a stable ``connection_id`` and can be upgraded to
    multiple accounts without changing the upload UI contract.
    """
    config = read_social_config() if config is None else config
    if isinstance(config, dict):
        migrate_brand_aliases(config)
        migrate_legacy_social_connections(config)
    raw_routes = config.get("brand_routes") if isinstance(config, dict) else None
    if not isinstance(raw_routes, dict):
        return {}

    result: dict[str, dict[str, dict[str, str]]] = {}
    for raw_brand, raw_platforms in raw_routes.items():
        brand = canonical_brand(raw_brand)
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
            route: dict[str, object] = {"connection_id": identity}
            if identity_key:
                route[identity_key] = identity
            connection = {}
            if platform in SOCIAL_CONNECTION_CONFIG_KEYS:
                connection = social_platform_connections(config, platform).get(identity, {})
                if not isinstance(connection, dict):
                    connection = {}
                owner = canonical_brand(connection.get("brand"))
                required = SOCIAL_CONNECTION_REQUIRED_KEYS[platform]
                configured = bool(connection) and owner == brand and all(
                    str(connection.get(key) or "").strip() for key in required
                )
                route["configured"] = configured
                route["connected"] = configured
                route["available"] = configured
                public_id_key = SOCIAL_CONNECTION_PUBLIC_ID_KEYS[platform]
                public_id = str(connection.get(public_id_key) or "").strip()
                if public_id:
                    route[public_id_key] = public_id
            name = str(
                raw_route.get("name")
                or raw_route.get("display_name")
                or connection.get("display_name")
                or connection.get("name")
                or ""
            ).strip()
            if name:
                route["name"] = name
            result.setdefault(brand, {}).setdefault(platform, route)
    return result


def social_platform_connections(config: dict, platform: str) -> dict[str, dict]:
    """Return the secret-bearing connection collection for one platform."""
    platform = str(platform or "").strip().casefold()
    section_key = SOCIAL_CONNECTION_CONFIG_KEYS.get(platform)
    if not section_key or not isinstance(config, dict):
        return {}
    section = config.get(section_key)
    connections = section.get("connections") if isinstance(section, dict) else None
    return connections if isinstance(connections, dict) else {}


def _brand_route_connection_id(config: dict, brand: str, platform: str) -> str:
    brand = canonical_brand(brand)
    routes = config.get("brand_routes") if isinstance(config, dict) else None
    brand_routes = routes.get(brand) if isinstance(routes, dict) else None
    route = brand_routes.get(platform) if isinstance(brand_routes, dict) else None
    if not isinstance(route, dict):
        return ""
    return str(route.get("connection_id") or route.get("account_id") or route.get("id") or "").strip()


def _legacy_connection_payload(section: dict, platform: str) -> dict:
    effective = dict(section) if isinstance(section, dict) else {}
    for key, env_name in SOCIAL_CONNECTION_ENV_KEYS[platform].items():
        env_value = str(os.environ.get(env_name) or "").strip()
        if env_value:
            effective[key] = env_value
    return {
        key: effective.get(key)
        for key in SOCIAL_CONNECTION_COPY_KEYS[platform]
        if effective.get(key) not in (None, "")
    }


def migrate_legacy_social_connections(config: dict) -> tuple[dict, bool]:
    """Idempotently bind legacy global accounts to Popsy only.

    Legacy fields remain in place for old settings screens. Their credentials
    are copied into the platform's named ``connections`` collection and never
    used as a fallback for another Brand.
    """
    if not isinstance(config, dict):
        return {}, False
    changed = False
    routes = config.get("brand_routes")
    if not isinstance(routes, dict):
        routes = {}
    popsy_routes = routes.get(LEGACY_SOCIAL_BRAND)
    if not isinstance(popsy_routes, dict):
        popsy_routes = {}

    for platform, section_key in SOCIAL_CONNECTION_CONFIG_KEYS.items():
        raw_section = config.get(section_key)
        section = dict(raw_section) if isinstance(raw_section, dict) else {}
        legacy = _legacy_connection_payload(section, platform)
        if not all(str(legacy.get(key) or "").strip() for key in SOCIAL_CONNECTION_REQUIRED_KEYS[platform]):
            continue
        connections = section.get("connections")
        if not isinstance(connections, dict):
            connections = {}
        connection_id = _brand_route_connection_id(config, LEGACY_SOCIAL_BRAND, platform)
        if not connection_id or connection_id == "global":
            connection_id = "popsy-legacy"
        existing = connections.get(connection_id)
        if not isinstance(existing, dict) or existing.get("_legacy_top_level"):
            migrated = dict(legacy)
            migrated["brand"] = LEGACY_SOCIAL_BRAND
            migrated["display_name"] = str(
                migrated.get("display_name") or SOCIAL_CONNECTION_DEFAULT_NAMES[platform]
            ).strip()
            migrated["_legacy_top_level"] = True
            if existing != migrated:
                connections[connection_id] = migrated
                changed = True
        section["connections"] = connections
        config[section_key] = section
        route = {
            "connection_id": connection_id,
            "name": str(
                connections.get(connection_id, {}).get("display_name")
                or SOCIAL_CONNECTION_DEFAULT_NAMES[platform]
            ).strip(),
        }
        if popsy_routes.get(platform) != route:
            popsy_routes[platform] = route
            changed = True

    if changed:
        routes[LEGACY_SOCIAL_BRAND] = popsy_routes
        config["brand_routes"] = routes
        try:
            config["brand_routes_version"] = int(config.get("brand_routes_version") or 0) + 1
        except (TypeError, ValueError):
            config["brand_routes_version"] = SOCIAL_BRAND_ROUTES_VERSION + 1
        config["brand_connections_version"] = SOCIAL_BRAND_CONNECTIONS_VERSION
    return config, changed


def store_social_brand_connection(
    config: dict,
    brand: str,
    platform: str,
    connection: dict,
    *,
    connection_id: str = "",
    name: str = "",
) -> str:
    """Store credentials in platform config and only the connection id in routes."""
    brand = canonical_brand(brand)
    platform = str(platform or "").strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
        raise ValueError("Brand chỉ được dùng chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.")
    if platform not in SOCIAL_CONNECTION_CONFIG_KEYS:
        raise ValueError(f"Unsupported Brand social platform: {platform or '<empty>'}.")
    migrate_brand_aliases(config)
    public_id_key = SOCIAL_CONNECTION_PUBLIC_ID_KEYS[platform]
    public_id = str(connection.get(public_id_key) or "").strip()
    requested_id = str(connection_id or "").strip()
    if requested_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", requested_id):
        raise ValueError("Social connection ID không hợp lệ.")

    connections = social_platform_connections(config, platform)
    if not requested_id:
        requested_id = next(
            (
                key for key, value in connections.items()
                if isinstance(value, dict)
                and canonical_brand(value.get("brand")) == brand
                and str(value.get(public_id_key) or "").strip() == public_id
            ),
            "",
        )
    if not requested_id:
        digest = hashlib.sha256(f"{platform}:{brand}:{public_id}".encode("utf-8")).hexdigest()[:12]
        requested_id = f"{platform}-{digest}"

    for candidate_id, candidate in connections.items():
        if not isinstance(candidate, dict) or str(candidate_id) == requested_id:
            continue
        candidate_public_id = str(candidate.get(public_id_key) or "").strip()
        if not public_id or candidate_public_id != public_id:
            continue
        candidate_owner = canonical_brand(candidate.get("brand"))
        if candidate_owner != brand:
            raise ValueError(
                f"Social account {public_id} đang thuộc brand {candidate_owner or 'khác'}."
            )

    existing = connections.get(requested_id)
    existing_owner = canonical_brand(existing.get("brand")) if isinstance(existing, dict) else ""
    if isinstance(existing, dict) and existing_owner != brand:
        raise ValueError(f"Social connection {requested_id} đang thuộc brand {existing_owner}.")

    value = dict(connection)
    value.pop("_brand_connection", None)
    value.pop("_connection_id", None)
    value.pop("_legacy_top_level", None)
    value["brand"] = brand
    display_name = str(name or value.get("display_name") or public_id or requested_id).strip()[:160]
    value["display_name"] = display_name
    connections[requested_id] = value
    section_key = SOCIAL_CONNECTION_CONFIG_KEYS[platform]
    section = config.get(section_key)
    section = dict(section) if isinstance(section, dict) else {}
    section["connections"] = connections
    config[section_key] = section

    routes = config.get("brand_routes")
    routes = dict(routes) if isinstance(routes, dict) else {}
    brand_routes = routes.get(brand)
    brand_routes = dict(brand_routes) if isinstance(brand_routes, dict) else {}
    brand_routes[platform] = {"connection_id": requested_id, "name": display_name}
    routes[brand] = brand_routes
    config["brand_routes"] = routes
    try:
        config["brand_routes_version"] = int(config.get("brand_routes_version") or 0) + 1
    except (TypeError, ValueError):
        config["brand_routes_version"] = SOCIAL_BRAND_ROUTES_VERSION + 1
    config["brand_connections_version"] = SOCIAL_BRAND_CONNECTIONS_VERSION
    return requested_id


def resolve_social_brand_connection(
    config: dict,
    brand: str,
    platform: str,
    *,
    requested_connection_id: str = "",
) -> tuple[str, dict]:
    """Resolve one explicit Brand route with no global/active fallback."""
    brand = canonical_brand(brand)
    platform = str(platform or "").strip().casefold()
    if not brand:
        raise ValueError(f"{platform.capitalize()} upload thiếu Brand.")
    if platform not in SOCIAL_CONNECTION_CONFIG_KEYS:
        raise ValueError(f"Unsupported Brand social platform: {platform or '<empty>'}.")
    migrate_brand_aliases(config)
    migrate_legacy_social_connections(config)
    connection_id = _brand_route_connection_id(config, brand, platform)
    if not connection_id:
        raise ValueError(f"Social route chưa cấu hình cho brand {brand} trên {platform}.")
    requested = str(requested_connection_id or "").strip()
    if requested and requested != connection_id:
        raise ValueError(f"Social connection không khớp route của brand {brand} trên {platform}.")
    connection = social_platform_connections(config, platform).get(connection_id)
    if not isinstance(connection, dict):
        raise ValueError(f"Social connection {connection_id} không tồn tại cho {platform}.")
    owner = canonical_brand(connection.get("brand"))
    if owner != brand:
        raise ValueError(f"Social connection {connection_id} không thuộc brand {brand}.")
    if not all(str(connection.get(key) or "").strip() for key in SOCIAL_CONNECTION_REQUIRED_KEYS[platform]):
        raise ValueError(f"Social connection {connection_id} chưa cấu hình đầy đủ cho {platform}.")
    resolved = dict(connection)
    resolved["_brand_connection"] = True
    resolved["_connection_id"] = connection_id
    return connection_id, resolved


def save_social_brand_route(
    brand: str,
    platform: str,
    connection_id: str,
    *,
    name: str = "",
    config: dict | None = None,
) -> dict[str, dict[str, str]]:
    """Persist one non-secret destination mapping for a Brand."""
    brand = canonical_brand(brand)
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
    if isinstance(config, dict):
        migrate_brand_aliases(config)
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
    if isinstance(config, dict):
        migrate_brand_aliases(config)
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
    brand = canonical_brand(brand)
    route = social_brand_routes(config).get(brand, {}).get(platform)
    if not route or not route.get(identity_key):
        raise ValueError(f"Social route chưa cấu hình cho brand {brand or '<empty>'} trên {platform}.")
    return route
