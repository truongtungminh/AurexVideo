from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen

from .config import canonical_brand, read_social_config, write_social_config


META_CONNECTIONS_KEY = "meta_connections"
META_GRAPH_BASE_URL = "https://graph.facebook.com"
META_PAGE_FIELDS = "id,name,access_token,tasks"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_graph_version(value: object) -> str:
    version = str(value or "v25.0").strip()
    if not version.startswith("v"):
        version = f"v{version}"
    if not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
        raise ValueError("Meta Graph API version không hợp lệ.")
    return version


def meta_graph_version(config: dict | None = None) -> str:
    config = read_social_config() if config is None else config
    facebook = config.get("facebook") if isinstance(config, dict) else {}
    facebook = facebook if isinstance(facebook, dict) else {}
    return _clean_graph_version(os.environ.get("META_GRAPH_API_VERSION") or facebook.get("graph_version") or "v25.0")


def meta_graph_url(config: dict, path: str) -> str:
    clean_path = str(path or "").strip().lstrip("/")
    if not clean_path or not re.fullmatch(r"[A-Za-z0-9_./-]+", clean_path):
        raise ValueError("Meta Graph path không hợp lệ.")
    return f"{META_GRAPH_BASE_URL}/{meta_graph_version(config)}/{clean_path}"


def _redact(value: object, *tokens: object) -> str:
    message = str(value or "")
    for raw_token in tokens:
        token = str(raw_token or "")
        if not token:
            continue
        message = message.replace(token, "[redacted]")
        message = message.replace(quote_plus(token), "[redacted]")
    return re.sub(
        r"(?i)(access_token|input_token)=([^&\s]+)",
        r"\1=[redacted]",
        message,
    )[:1000]


class MetaGraphError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "graph_error", http_status: int = 502, code: int = 0):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status
        self.code = code


def _graph_error(payload: object, http_status: int, *tokens: object) -> MetaGraphError:
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    try:
        code = int(error.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    message = _redact(error.get("message") or f"Meta Graph API trả HTTP {http_status}.", *tokens)
    if http_status == 429 or code in {4, 17, 32, 613}:
        return MetaGraphError(message, kind="rate_limit", http_status=429, code=code)
    if code == 190 or http_status == 401:
        return MetaGraphError(message, kind="invalid_token", http_status=401, code=code)
    if code in {10, 200, 294} or http_status == 403:
        return MetaGraphError(message, kind="insufficient_permissions", http_status=403, code=code)
    return MetaGraphError(message, kind="graph_error", http_status=max(400, http_status or 502), code=code)


def _request_json(url: str, params: dict | None = None, *, redaction_tokens: tuple[object, ...] = ()) -> dict:
    query = urlencode(params or {})
    request_url = f"{url}{'&' if '?' in url else '?'}{query}" if query else url
    request = Request(request_url, headers={"Accept": "application/json", "User-Agent": "AurexVideo/MetaSync"}, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", "replace")
        finally:
            exc.close()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"error": {"message": raw[:500]}}
        raise _graph_error(payload, exc.code, *redaction_tokens) from exc
    except URLError as exc:
        raise MetaGraphError(
            _redact(exc.reason or exc, *redaction_tokens),
            kind="network_error",
            http_status=502,
        ) from exc
    except TimeoutError as exc:
        raise MetaGraphError("Meta Graph API quá thời gian phản hồi.", kind="network_error", http_status=504) from exc
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise MetaGraphError("Meta Graph API trả dữ liệu JSON không hợp lệ.", kind="malformed_response", http_status=502) from exc
    if not isinstance(payload, dict):
        raise MetaGraphError("Meta Graph API trả response không hợp lệ.", kind="malformed_response", http_status=502)
    if isinstance(payload.get("error"), dict):
        raise _graph_error(payload, 400, *redaction_tokens)
    return payload


def _graph_get(
    config: dict,
    path: str,
    system_user_token: str,
    params: dict | None = None,
    *,
    authorization_token: str = "",
) -> dict:
    auth_token = str(authorization_token or system_user_token or "").strip()
    fields = dict(params or {})
    fields["access_token"] = auth_token
    return _request_json(
        meta_graph_url(config, path),
        fields,
        redaction_tokens=(system_user_token, auth_token),
    )


def _graph_get_next(url: str, system_user_token: str) -> dict:
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or parsed.hostname != "graph.facebook.com":
        raise MetaGraphError("Meta pagination URL không hợp lệ.", kind="malformed_response", http_status=502)
    return _request_json(str(url), redaction_tokens=(system_user_token,))


def _validate_system_user_token(value: object) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or re.search(r"\s", token):
        raise ValueError("System User Access Token không hợp lệ.")
    return token


def _normalize_page(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Meta trả một Page record không hợp lệ.")
    page_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    access_token = str(raw.get("access_token") or "").strip()
    tasks_raw = raw.get("tasks")
    tasks = []
    if isinstance(tasks_raw, list):
        tasks = list(dict.fromkeys(str(item or "").strip().upper() for item in tasks_raw if str(item or "").strip()))
    if not re.fullmatch(r"[0-9]+", page_id):
        raise ValueError("Meta Page ID không hợp lệ.")
    if not name:
        raise ValueError(f"Page {page_id} không có tên.")
    if len(access_token) < 20 or re.search(r"\s", access_token):
        raise ValueError(f"Page {page_id} không có Page Access Token hợp lệ.")
    return {
        "id": page_id,
        "name": name[:160],
        "page_access_token": access_token,
        "tasks": tasks,
    }


def fetch_accessible_pages(system_user_token: str, config: dict | None = None) -> dict:
    token = _validate_system_user_token(system_user_token)
    config = read_social_config() if config is None else config
    next_url = ""
    visited: set[str] = set()
    pages_by_id: dict[str, dict] = {}
    errors: list[dict] = []
    examined = 0
    complete = True

    for page_number in range(1, 101):
        try:
            payload = (
                _graph_get_next(next_url, token)
                if next_url
                else _graph_get(config, "me/accounts", token, {"fields": META_PAGE_FIELDS, "limit": "100"})
            )
        except MetaGraphError as exc:
            if not pages_by_id:
                raise
            errors.append({"kind": exc.kind, "page": page_number, "error": _redact(exc, token)})
            complete = False
            break

        records = payload.get("data")
        if not isinstance(records, list):
            if not pages_by_id:
                raise MetaGraphError("Meta /me/accounts không trả danh sách data.", kind="malformed_response", http_status=502)
            errors.append({"kind": "malformed_response", "page": page_number, "error": "Meta pagination không trả danh sách data."})
            complete = False
            break
        for raw in records:
            examined += 1
            try:
                page = _normalize_page(raw)
                pages_by_id[page["id"]] = page
            except ValueError as exc:
                page_id = str(raw.get("id") or "") if isinstance(raw, dict) else ""
                errors.append({"kind": "malformed_page", "page_id": page_id, "error": _redact(exc, token)})

        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        next_url = str(paging.get("next") or "").strip()
        if not next_url:
            break
        if next_url in visited:
            errors.append({"kind": "pagination_failure", "page": page_number, "error": "Meta pagination bị lặp URL."})
            complete = False
            break
        visited.add(next_url)
    else:
        errors.append({"kind": "pagination_failure", "page": 100, "error": "Meta pagination vượt giới hạn an toàn."})
        complete = False

    return {
        "pages": list(pages_by_id.values()),
        "examined": examined,
        "errors": errors,
        "complete": complete,
    }


def _connections(config: dict) -> dict[str, dict]:
    raw = config.get(META_CONNECTIONS_KEY) if isinstance(config, dict) else None
    return raw if isinstance(raw, dict) else {}


def _page_brand_map(config: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    routes = config.get("brand_routes") if isinstance(config, dict) else None
    if not isinstance(routes, dict):
        return result
    for raw_brand, raw_routes in routes.items():
        brand = canonical_brand(raw_brand)
        route = raw_routes.get("facebook") if isinstance(raw_routes, dict) else None
        page_id = str(route.get("page_id") or route.get("connection_id") or "").strip() if isinstance(route, dict) else ""
        if brand and page_id:
            result.setdefault(page_id, []).append(brand)
    return result


def _public_connection(record: dict, config: dict) -> dict:
    connection_id = str(record.get("id") or "").strip()
    pages = [
        page for page in (config.get("facebook", {}).get("pages") or [])
        if isinstance(page, dict) and str(page.get("meta_connection_id") or "") == connection_id
    ] if isinstance(config.get("facebook"), dict) else []
    token = str(record.get("system_user_access_token") or "")
    token_hint = f"{token[:4]}••••{token[-4:]}" if len(token) >= 12 else ("Đã lưu" if token else "")
    return {
        "id": connection_id,
        "name": str(record.get("name") or connection_id),
        "business_id": str(record.get("business_id") or ""),
        "system_user_id": str(record.get("system_user_id") or ""),
        "app_id": str(record.get("app_id") or ""),
        "token_status": str(record.get("token_status") or "unchecked"),
        "token_expires_at": record.get("token_expires_at") or 0,
        "token_expiry_known": bool(record.get("token_expiry_known")),
        "last_checked_at": str(record.get("last_checked_at") or ""),
        "last_synced_at": str(record.get("last_synced_at") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "token_configured": bool(token),
        "token_hint": token_hint,
        "page_count": len(pages),
        "active_page_count": sum(str(page.get("status") or "active") == "active" for page in pages),
        "inaccessible_page_count": sum(str(page.get("status") or "active") == "inaccessible" for page in pages),
        "accessible_pages_count": int(record.get("accessible_pages_count") or 0),
        "scopes": list(record.get("scopes") or []) if isinstance(record.get("scopes"), list) else [],
        "granular_scopes": list(record.get("granular_scopes") or []) if isinstance(record.get("granular_scopes"), list) else [],
        "last_error": str(record.get("last_error") or ""),
    }


def list_meta_connections(config: dict | None = None) -> list[dict]:
    config = read_social_config() if config is None else config
    records = [_public_connection(record, config) for record in _connections(config).values() if isinstance(record, dict)]
    records.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return records


def save_meta_connection(
    name: str,
    system_user_access_token: str = "",
    *,
    connection_id: str = "",
    business_id: str = "",
    config: dict | None = None,
    persist: bool = True,
) -> dict:
    config = read_social_config() if config is None else config
    connections = dict(_connections(config))
    name = str(name or "").strip()
    connection_id = str(connection_id or "").strip()
    business_id = str(business_id or "").strip()
    if not name or len(name) > 160:
        raise ValueError("Tên Meta Connection phải có từ 1 đến 160 ký tự.")
    if connection_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", connection_id):
        raise ValueError("Meta Connection ID không hợp lệ.")
    if business_id and not re.fullmatch(r"[0-9]+", business_id):
        raise ValueError("Meta Business ID chỉ được chứa số.")
    existing = connections.get(connection_id) if connection_id else None
    if connection_id and not isinstance(existing, dict):
        raise FileNotFoundError(f"Meta Connection {connection_id} không tồn tại.")
    token = str(system_user_access_token or "").strip()
    if token:
        token = _validate_system_user_token(token)
    elif isinstance(existing, dict):
        token = str(existing.get("system_user_access_token") or "").strip()
    if not token:
        raise ValueError("System User Access Token không được để trống.")
    if not connection_id:
        connection_id = f"meta-{uuid.uuid4().hex[:12]}"
    now = _utc_now()
    record = dict(existing) if isinstance(existing, dict) else {}
    token_changed = bool(existing) and token != str(existing.get("system_user_access_token") or "")
    record.update({
        "id": connection_id,
        "name": name,
        "business_id": business_id,
        "system_user_access_token": token,
        "created_at": str(record.get("created_at") or now),
        "updated_at": now,
    })
    if not existing or token_changed:
        record.update({
            "token_status": "unchecked",
            "token_expires_at": 0,
            "token_expiry_known": False,
            "last_checked_at": "",
            "last_error": "",
        })
    connections[connection_id] = record
    config[META_CONNECTIONS_KEY] = connections
    if persist:
        write_social_config(config)
    return _public_connection(record, config)


def _invalid_diagnostic(message: str, checked_at: str) -> dict:
    return {
        "valid": False,
        "app_id": "",
        "system_user_id": "",
        "expires_at": 0,
        "expiry_known": False,
        "scopes": [],
        "granular_scopes": [],
        "accessible_pages_count": 0,
        "checked_at": checked_at,
        "error": message,
    }


def diagnose_system_user_token(system_user_access_token: str, config: dict | None = None) -> dict:
    token = _validate_system_user_token(system_user_access_token)
    config = read_social_config() if config is None else config
    checked_at = _utc_now()
    debug_data: dict = {}
    debug_warning = ""
    debug_auth = str(os.environ.get("META_APP_ACCESS_TOKEN") or token).strip()
    try:
        debug_payload = _graph_get(
            config,
            "debug_token",
            token,
            {"input_token": token},
            authorization_token=debug_auth,
        )
        debug_data = debug_payload.get("data") if isinstance(debug_payload.get("data"), dict) else {}
        if debug_data and debug_data.get("is_valid") is False:
            error = debug_data.get("error") if isinstance(debug_data.get("error"), dict) else {}
            return _invalid_diagnostic(_redact(error.get("message") or "System User Token không hợp lệ.", token), checked_at)
    except MetaGraphError as exc:
        if exc.kind in {"rate_limit", "network_error"}:
            raise
        debug_warning = _redact(exc, token)

    try:
        me = _graph_get(config, "me", token, {"fields": "id,name"})
    except MetaGraphError as exc:
        if exc.kind == "invalid_token":
            return _invalid_diagnostic(_redact(exc, token), checked_at)
        raise
    system_user_id = str(debug_data.get("user_id") or me.get("id") or "").strip()
    if not system_user_id:
        return _invalid_diagnostic("Meta không trả System User ID.", checked_at)

    pages_count = 0
    pages_error = ""
    try:
        pages_result = fetch_accessible_pages(token, config)
        pages_count = len(pages_result["pages"])
        if pages_result["errors"]:
            pages_error = str(pages_result["errors"][0].get("error") or "")
    except MetaGraphError as exc:
        if exc.kind in {"rate_limit", "network_error", "invalid_token"}:
            raise
        pages_error = _redact(exc, token)

    expires_at = debug_data.get("expires_at") or 0
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        expires_at = 0
    scopes = debug_data.get("scopes") if isinstance(debug_data.get("scopes"), list) else []
    granular = debug_data.get("granular_scopes") if isinstance(debug_data.get("granular_scopes"), list) else []
    result = {
        "valid": True,
        "app_id": str(debug_data.get("app_id") or ""),
        "system_user_id": system_user_id,
        "system_user_name": str(me.get("name") or ""),
        "expires_at": expires_at,
        "expiry_known": "expires_at" in debug_data,
        "scopes": [str(item) for item in scopes],
        "granular_scopes": granular,
        "accessible_pages_count": pages_count,
        "checked_at": checked_at,
        "error": pages_error,
        "warning": debug_warning,
    }
    return result


def _connection_record(config: dict, connection_id: str) -> dict:
    connection_id = str(connection_id or "").strip()
    record = _connections(config).get(connection_id)
    if not connection_id or not isinstance(record, dict):
        raise FileNotFoundError(f"Meta Connection {connection_id or '<empty>'} không tồn tại.")
    return record


def diagnose_meta_connection(connection_id: str, config: dict | None = None, *, persist: bool = True) -> dict:
    config = read_social_config() if config is None else config
    record = _connection_record(config, connection_id)
    token = _validate_system_user_token(record.get("system_user_access_token"))
    try:
        result = diagnose_system_user_token(token, config)
    except MetaGraphError as exc:
        record["last_checked_at"] = _utc_now()
        record["token_status"] = "invalid" if exc.kind == "invalid_token" else "error"
        record["last_error"] = _redact(exc, token)
        record["updated_at"] = record["last_checked_at"]
        if persist:
            write_social_config(config)
        raise
    record.update({
        "system_user_id": str(result.get("system_user_id") or record.get("system_user_id") or ""),
        "app_id": str(result.get("app_id") or record.get("app_id") or ""),
        "token_status": "valid" if result.get("valid") else "invalid",
        "token_expires_at": result.get("expires_at") or 0,
        "token_expiry_known": bool(result.get("expiry_known")),
        "last_checked_at": str(result.get("checked_at") or _utc_now()),
        "accessible_pages_count": int(result.get("accessible_pages_count") or 0),
        "scopes": list(result.get("scopes") or []),
        "granular_scopes": list(result.get("granular_scopes") or []),
        "last_error": str(result.get("error") or ""),
        "updated_at": _utc_now(),
    })
    if persist:
        write_social_config(config)
    return result


def list_meta_pages(connection_id: str, config: dict | None = None) -> list[dict]:
    config = read_social_config() if config is None else config
    _connection_record(config, connection_id)
    facebook = config.get("facebook") if isinstance(config.get("facebook"), dict) else {}
    pages = facebook.get("pages") if isinstance(facebook.get("pages"), list) else []
    brands = _page_brand_map(config)
    result = []
    for page in pages:
        if not isinstance(page, dict) or str(page.get("meta_connection_id") or "") != connection_id:
            continue
        page_id = str(page.get("id") or page.get("page_id") or "").strip()
        status = str(page.get("status") or "active")
        token_configured = bool(str(page.get("page_access_token") or page.get("access_token") or "").strip())
        result.append({
            "id": page_id,
            "name": str(page.get("name") or page_id),
            "thumbnail": str(page.get("thumbnail") or ""),
            "tasks": list(page.get("tasks") or []) if isinstance(page.get("tasks"), list) else [],
            "status": status,
            "token_status": "active" if token_configured and status == "active" else status if status == "inaccessible" else "missing",
            "token_configured": token_configured,
            "meta_connection_id": connection_id,
            "brands": list(brands.get(page_id) or []),
            "brand": str((brands.get(page_id) or [""])[0]),
            "last_synced_at": str(page.get("last_synced_at") or ""),
        })
    result.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return result


def sync_meta_pages(connection_id: str, config: dict | None = None, *, persist: bool = True) -> dict:
    config = read_social_config() if config is None else config
    record = _connection_record(config, connection_id)
    token = _validate_system_user_token(record.get("system_user_access_token"))
    now = _utc_now()
    try:
        fetched = fetch_accessible_pages(token, config)
    except MetaGraphError as exc:
        record["last_checked_at"] = now
        record["token_status"] = "invalid" if exc.kind == "invalid_token" else "error"
        record["last_error"] = _redact(exc, token)
        record["updated_at"] = now
        if persist:
            write_social_config(config)
        raise

    facebook = config.get("facebook")
    facebook = dict(facebook) if isinstance(facebook, dict) else {}
    raw_pages = facebook.get("pages") if isinstance(facebook.get("pages"), list) else []
    ordered_ids: list[str] = []
    existing_by_id: dict[str, dict] = {}
    for raw in raw_pages:
        if not isinstance(raw, dict):
            continue
        page_id = str(raw.get("id") or raw.get("page_id") or "").strip()
        if not page_id:
            continue
        if page_id not in existing_by_id:
            ordered_ids.append(page_id)
        existing_by_id[page_id] = dict(raw)

    imported = 0
    updated = 0
    fetched_ids: set[str] = set()
    for page in fetched["pages"]:
        page_id = page["id"]
        fetched_ids.add(page_id)
        existing = existing_by_id.get(page_id)
        if existing is None:
            imported += 1
            ordered_ids.append(page_id)
            existing = {}
        else:
            updated += 1
        existing.update({
            "id": page_id,
            "name": page["name"],
            "page_access_token": page["page_access_token"],
            "tasks": page["tasks"],
            "meta_connection_id": connection_id,
            "status": "active",
            "last_synced_at": now,
        })
        existing_by_id[page_id] = existing

    inaccessible = 0
    if fetched["complete"]:
        for page_id, page in existing_by_id.items():
            if str(page.get("meta_connection_id") or "") != connection_id or page_id in fetched_ids:
                continue
            if str(page.get("status") or "active") != "inaccessible":
                inaccessible += 1
            page["status"] = "inaccessible"
            page["last_synced_at"] = now

    facebook["pages"] = [existing_by_id[page_id] for page_id in ordered_ids if page_id in existing_by_id]
    current_active_id = str(facebook.get("active_page_id") or "").strip()
    current_active = existing_by_id.get(current_active_id)
    if not current_active or str(current_active.get("status") or "active") == "inaccessible":
        active_ids = [
            page_id for page_id in ordered_ids
            if str(existing_by_id[page_id].get("status") or "active") != "inaccessible"
            and str(existing_by_id[page_id].get("page_access_token") or "").strip()
        ]
        facebook["active_page_id"] = active_ids[0] if active_ids else ""
    facebook.setdefault("graph_version", meta_graph_version(config))
    facebook.setdefault("video_state", "PUBLISHED")
    config["facebook"] = facebook
    record.update({
        "token_status": "valid",
        "last_checked_at": now,
        "last_synced_at": now,
        "accessible_pages_count": len(fetched["pages"]),
        "last_error": str(fetched["errors"][0].get("error") or "") if fetched["errors"] else "",
        "updated_at": now,
    })
    if persist:
        write_social_config(config)
    errors = [
        {key: _redact(value, token) if key == "error" else value for key, value in error.items()}
        for error in fetched["errors"]
    ]
    return {
        "ok": not errors,
        "partial": bool(errors) and bool(imported or updated),
        "connection_id": connection_id,
        "total": imported + updated + len(errors),
        "imported": imported,
        "updated": updated,
        "failed": len(errors),
        "inaccessible": inaccessible,
        "complete": bool(fetched["complete"]),
        "last_synced_at": now,
        "errors": errors,
        "pages": list_meta_pages(connection_id, config),
    }


__all__ = [
    "MetaGraphError",
    "diagnose_meta_connection",
    "diagnose_system_user_token",
    "fetch_accessible_pages",
    "list_meta_connections",
    "list_meta_pages",
    "meta_graph_url",
    "meta_graph_version",
    "save_meta_connection",
    "sync_meta_pages",
]
