from __future__ import annotations

"""Shopee Affiliate Open API adapter.

Only the official Open API surface is used here.  Product discovery and short
link creation go through the GraphQL endpoint; the engine never scrapes the
Shopee storefront.  Credentials remain in the existing protected social
config and are never returned by the status endpoints.
"""

import hashlib
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import (
    canonical_brand,
    read_social_config,
    resolve_social_brand_connection,
    store_social_brand_connection,
    write_social_config,
)
from . import affiliate_store


DEFAULT_SHOPEE_API_BASE_URL = "https://open-api.affiliate.shopee.vn/graphql"
MAX_SUB_IDS = 5
MAX_PRODUCT_LIMIT = 50
SHOPEE_HOSTS = {
    "shopee.vn",
    "www.shopee.vn",
    "s.shopee.vn",
    "shopee.ee",
    "shope.ee",
}


class ShopeeApiError(RuntimeError):
    """Safe provider error without credential material."""


def shopee_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    value = config.get("shopee", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def resolve_shopee_config(value: dict | None = None) -> dict:
    saved = value if isinstance(value, dict) else shopee_config()
    use_environment = not bool(saved.get("_brand_connection"))
    return {
        "app_id": str((os.environ.get("SHOPEE_AFFILIATE_APP_ID") if use_environment else "") or saved.get("app_id") or "").strip(),
        "secret": str((os.environ.get("SHOPEE_AFFILIATE_SECRET") if use_environment else "") or saved.get("secret") or "").strip(),
        "api_base_url": str(
            (os.environ.get("SHOPEE_AFFILIATE_API_BASE_URL") if use_environment else "")
            or saved.get("api_base_url")
            or DEFAULT_SHOPEE_API_BASE_URL
        ).strip().rstrip("/"),
    }


def shopee_is_configured(value: dict | None = None) -> bool:
    resolved = resolve_shopee_config(value)
    return bool(resolved["app_id"] and resolved["secret"])


def _masked_secret(secret: str) -> str:
    return f"{secret[:3]}…{secret[-3:]}" if len(secret) >= 8 else ("đã cấu hình" if secret else "")


def shopee_config_hint() -> str:
    return "Shopee Affiliate chưa cấu hình. Nhập App ID và Secret trong Affiliate Dashboard."


def shopee_status(value: dict | None = None) -> dict:
    saved = value if isinstance(value, dict) else shopee_config()
    resolved = resolve_shopee_config(saved)
    configured = bool(resolved["app_id"] and resolved["secret"])
    return {
        "configured": configured,
        "connected": configured,
        "available": configured,
        "ready": configured,
        "app_id": resolved["app_id"],
        "api_base_url": resolved["api_base_url"],
        "connection_id": str(saved.get("_connection_id") or saved.get("connection_id") or "").strip(),
        "display_name": str(saved.get("display_name") or saved.get("name") or "").strip(),
        "masked_secret": _masked_secret(resolved["secret"]),
        "message": "" if configured else shopee_config_hint(),
    }


def shopee_status_for_brand(config: dict, brand: str) -> dict:
    brand = canonical_brand(brand)
    if not brand:
        return {"brand": "", **shopee_status({"_brand_connection": True}), "settings": {}}
    try:
        connection_id, connection = resolve_social_brand_connection(config, brand, "shopee")
    except (ValueError, KeyError) as exc:
        return {
            "brand": brand,
            **shopee_status({"_brand_connection": True}),
            "connection_id": "",
            "message": str(exc),
            "settings": affiliate_store.get_settings(brand),
        }
    status = shopee_status({**connection, "_connection_id": connection_id, "_brand_connection": True})
    status["brand"] = brand
    status["settings"] = affiliate_store.get_settings(brand)
    return status


def update_shopee_config(
    app_id: str,
    secret: str,
    *,
    api_base_url: str = DEFAULT_SHOPEE_API_BASE_URL,
    brand: str = "",
    connection_id: str = "",
    display_name: str = "",
    config: dict | None = None,
    persist: bool = True,
) -> dict:
    app_id = str(app_id or "").strip()
    secret = str(secret or "").strip()
    api_base_url = str(api_base_url or DEFAULT_SHOPEE_API_BASE_URL).strip().rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,128}", app_id):
        raise ValueError("Shopee App ID không hợp lệ.")
    if len(secret) < 16 or re.search(r"\s", secret):
        raise ValueError("Shopee Affiliate Secret có vẻ không hợp lệ.")
    parsed = urlparse(api_base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Shopee Affiliate API URL phải là HTTPS và không có query string.")

    config = read_social_config() if config is None else config
    value = {
        "app_id": app_id,
        "secret": secret,
        "api_base_url": api_base_url,
        "display_name": str(display_name or "").strip(),
    }
    brand = canonical_brand(brand)
    if brand:
        saved_id = store_social_brand_connection(
            config,
            brand,
            "shopee",
            value,
            connection_id=connection_id,
            name=display_name or "Shopee Affiliate",
        )
        if persist:
            write_social_config(config)
        account = affiliate_store.upsert_account(brand, "shopee", saved_id, api_base_url)
        result = shopee_status({**value, "_brand_connection": True, "_connection_id": saved_id})
        result.update({"ok": True, "brand": brand, "connection_id": saved_id, "account": account})
        return result

    existing = shopee_config(config)
    if isinstance(existing.get("connections"), dict):
        value["connections"] = existing["connections"]
    config["shopee"] = value
    if persist:
        write_social_config(config)
    return {"ok": True, **shopee_status(value)}


def disconnect_shopee(brand: str = "") -> dict:
    brand = canonical_brand(brand)
    config = read_social_config()
    section = shopee_config(config)
    connections = section.get("connections") if isinstance(section, dict) else None
    if brand:
        route = (config.get("brand_routes") or {}).get(brand, {}) if isinstance(config.get("brand_routes"), dict) else {}
        route = route.get("shopee") if isinstance(route, dict) else None
        connection_id = str((route or {}).get("connection_id") or "").strip()
        if connection_id and isinstance(connections, dict):
            connection = connections.get(connection_id)
            if isinstance(connection, dict) and canonical_brand(connection.get("brand")) == brand:
                connections.pop(connection_id, None)
        routes = config.get("brand_routes")
        if isinstance(routes, dict) and isinstance(routes.get(brand), dict):
            routes[brand].pop("shopee", None)
            if not routes[brand]:
                routes.pop(brand, None)
            config["brand_routes"] = routes
        if isinstance(connections, dict) and connections:
            section = {**section, "connections": connections}
            config["shopee"] = section
        else:
            config.pop("shopee", None)
        affiliate_store.delete_account(brand, "shopee")
    else:
        config.pop("shopee", None)
    write_social_config(config)
    return {"ok": True, "brand": brand, "configured": False, "connected": False}


def validate_shopee_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in SHOPEE_HOSTS or not parsed.path:
        raise ValueError("Origin URL phải là link Shopee HTTPS hợp lệ.")
    return raw


def _graphql_request(connection: dict, query: str, variables: dict | None = None) -> dict:
    resolved = resolve_shopee_config(connection)
    if not resolved["app_id"] or not resolved["secret"]:
        raise ValueError(shopee_config_hint())
    body = {"query": query, "variables": variables or {}}
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    signature = hashlib.sha256(
        f"{resolved['app_id']}{timestamp}{encoded.decode('utf-8')}{resolved['secret']}".encode("utf-8")
    ).hexdigest()
    headers = {
        "Authorization": f"SHA256 Credential={resolved['app_id']}, Signature={signature}, Timestamp={timestamp}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AurexVideo/affiliate-engine",
    }
    request = Request(resolved["api_base_url"], data=encoded, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        finally:
            exc.close()
        raise ShopeeApiError(f"Shopee Affiliate API HTTP {exc.code}: {detail[:800]}") from exc
    except URLError as exc:
        raise ShopeeApiError(f"Shopee Affiliate API request failed: {exc}") from exc
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise ShopeeApiError("Shopee Affiliate API trả về JSON không hợp lệ.") from exc
    if not isinstance(parsed, dict):
        raise ShopeeApiError("Shopee Affiliate API trả về dữ liệu không hợp lệ.")
    errors = parsed.get("errors")
    if errors:
        messages = []
        for error in errors if isinstance(errors, list) else [errors]:
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("extensions", {}).get("message") or "").strip()
            else:
                message = str(error).strip()
            if message:
                messages.append(message[:300])
        raise ShopeeApiError("Shopee Affiliate API error: " + "; ".join(messages or ["unknown error"]))
    return parsed


def generate_short_link(connection: dict, origin_url: str, sub_ids: list[str] | None = None) -> str:
    origin_url = validate_shopee_url(origin_url)
    sub_ids = [str(value or "").strip() for value in (sub_ids or []) if str(value or "").strip()]
    if len(sub_ids) > MAX_SUB_IDS:
        raise ValueError(f"Shopee chỉ nhận tối đa {MAX_SUB_IDS} SubID.")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) for value in sub_ids):
        raise ValueError("Shopee SubID chỉ được dùng chữ, số, gạch ngang hoặc gạch dưới.")
    query = """
      mutation GenerateShortLink($input: ShortLinkInput!) {
        generateShortLink(input: $input) { shortLink }
      }
    """
    result = _graphql_request(connection, query, {"input": {"originUrl": origin_url, "subIds": sub_ids}})
    link = result.get("data", {}).get("generateShortLink", {}).get("shortLink") if isinstance(result.get("data"), dict) else ""
    link = str(link or "").strip()
    if not link:
        raise ShopeeApiError("Shopee Affiliate API không trả về short link.")
    return link


def search_product_offers(connection: dict, keyword: str, limit: int = 10) -> list[dict]:
    keyword = re.sub(r"\s+", " ", str(keyword or "")).strip()
    if len(keyword) < 2:
        raise ValueError("Từ khoá sản phẩm Shopee cần ít nhất 2 ký tự.")
    limit = max(1, min(int(limit or 10), MAX_PRODUCT_LIMIT))
    query = """
      query ProductOfferV2($keyword: String!, $limit: Int!) {
        productOfferV2(keyword: $keyword, limit: $limit) {
          nodes {
            productName
            itemId
            shopId
            commissionRate
            sales
            priceMin
            priceMax
            imageUrl
            productLink
            offerLink
          }
        }
      }
    """
    result = _graphql_request(connection, query, {"keyword": keyword, "limit": limit})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    offers = data.get("productOfferV2") if isinstance(data, dict) else {}
    nodes = offers.get("nodes") if isinstance(offers, dict) else offers
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []
