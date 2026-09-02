from __future__ import annotations

"""Provider-neutral orchestration for AurexVideo affiliate publishing."""

import math
import hashlib
import random
import re
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import affiliate_store
from .config import canonical_brand, read_social_config, resolve_social_brand_connection
from .shopee import (
    SHOPEE_HOSTS,
    generate_short_link,
    search_product_offers,
    shopee_status_for_brand,
    validate_shopee_url,
)


AFFILIATE_MODES = {"off", "manual", "auto"}
AFFILIATE_PLACEMENTS = {"first_comment", "caption", "caption_and_comment", "shopee_native_tag"}


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip().replace(",", "")
    raw = re.sub(r"[^0-9.\-]", "", raw)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _fraction(value: object) -> float:
    number = _float(value)
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def _tokens(value: object) -> list[str]:
    return [token for token in re.findall(r"[\wÀ-ỹ]+", str(value or "").casefold(), flags=re.UNICODE) if len(token) > 1]


def _token_relevance(query: str, name: str) -> float:
    query_tokens = Counter(_tokens(query))
    product_tokens = Counter(_tokens(name))
    if not query_tokens or not product_tokens:
        return 0.0
    overlap = sum((query_tokens & product_tokens).values())
    return max(0.0, min(1.0, overlap / max(1, sum(query_tokens.values()))))


def _safe_slug(value: object, fallback: str = "unknown") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-").lower()
    return (slug or fallback)[:64]


def build_sub_ids(
    brand: object,
    page_id: object,
    content_id: object,
    product_id: object,
    placement: object,
) -> list[str]:
    """Build the five stable tracking dimensions supported by Shopee."""
    return [
        _safe_slug(brand),
        _safe_slug(page_id, "facebook"),
        _safe_slug(content_id, "content"),
        _safe_slug(product_id, "product"),
        _safe_slug(placement, "first-comment"),
    ]


def normalize_product(raw: dict, query: str = "") -> dict:
    name = str(raw.get("name") or raw.get("productName") or raw.get("product_name") or "").strip()
    provider_product_id = str(
        raw.get("provider_product_id")
        or raw.get("productId")
        or raw.get("itemId")
        or raw.get("item_id")
        or ""
    ).strip()
    shop_id = str(raw.get("shop_id") or raw.get("shopId") or "").strip()
    origin_url = str(
        raw.get("origin_url")
        or raw.get("original_url")
        or raw.get("productLink")
        or raw.get("product_link")
        or raw.get("productUrl")
        or raw.get("product_url")
        or raw.get("offer_url")
        or ""
    ).strip()
    if not origin_url and re.fullmatch(r"[1-9][0-9]{0,31}", shop_id) and re.fullmatch(
        r"[1-9][0-9]{0,31}", provider_product_id
    ):
        origin_url = f"https://shopee.vn/product/{shop_id}/{provider_product_id}"
    if not origin_url:
        # Legacy payloads sometimes expose only the provider's offer URL.
        origin_url = str(raw.get("offerLink") or raw.get("offer_url") or "").strip()
    explicit_relevance = raw.get("relevance_score", raw.get("relevanceScore", raw.get("relevance")))
    relevance = _fraction(explicit_relevance) if explicit_relevance is not None else _token_relevance(query, name)
    historical_conversion = _fraction(
        raw.get(
            "historical_conversion",
            raw.get("historicalConversion", raw.get("historical_conversion_rate", raw.get("conversionRate", 0))),
        )
    )
    return {
        "provider": "shopee",
        "provider_product_id": provider_product_id,
        "shop_id": shop_id,
        "name": name or "Shopee product",
        "origin_url": origin_url,
        "offer_url": str(raw.get("offer_url") or raw.get("offerLink") or "").strip(),
        "image_url": str(raw.get("image_url") or raw.get("imageUrl") or "").strip(),
        "price_min": _float(raw.get("price_min") or raw.get("priceMin") or raw.get("price")),
        "price_max": _float(raw.get("price_max") or raw.get("priceMax") or raw.get("price")),
        "commission_rate": _fraction(raw.get("commission_rate", raw.get("commissionRate"))),
        "sales": max(0.0, _float(raw.get("sales") if raw.get("sales") not in (None, "") else raw.get("soldCount"))),
        "rating": max(0.0, min(5.0, _float(raw.get("rating", raw.get("ratingStar"))))),
        "discount_rate": max(0.0, min(100.0, _float(raw.get("discount_rate", raw.get("priceDiscountRate"))))),
        "shop_quality": _fraction(raw.get("shop_quality", raw.get("shopQuality"))),
        "relevance_score": relevance,
        "historical_conversion": historical_conversion,
        "raw": raw,
    }


def rank_products(products: list[dict], query: str = "") -> list[dict]:
    """Rank by relevance first, while still rewarding commercial quality."""
    normalized = [normalize_product(product, query) for product in products if isinstance(product, dict)]
    max_sales = max((product["sales"] for product in normalized), default=0.0)
    max_commission = max((product["commission_rate"] for product in normalized), default=0.0)
    for product in normalized:
        relevance = product["relevance_score"]
        commission_score = (product["commission_rate"] / max_commission) if max_commission else 0.0
        sales_score = (
            math.log1p(product["sales"]) / math.log1p(max_sales)
            if max_sales > 0 and product["sales"] > 0
            else 0.0
        )
        rating_score = product["rating"] / 5 if product["rating"] else 0.0
        discount_score = product["discount_rate"] / 100 if product["discount_rate"] else 0.0
        historical_conversion_score = product["historical_conversion"]
        product["ranking_score"] = round(
            relevance * 0.40
            + commission_score * 0.20
            + sales_score * 0.15
            + rating_score * 0.10
            + discount_score * 0.05
            + product["shop_quality"] * 0.05
            + historical_conversion_score * 0.05,
            6,
        )
    return sorted(normalized, key=lambda product: (-product["ranking_score"], -product["relevance_score"], product["name"].casefold()))


def normalize_settings(values: dict | None = None, current: dict | None = None) -> dict:
    current = current if isinstance(current, dict) else {}
    values = values if isinstance(values, dict) else {}
    merged = {**affiliate_store.DEFAULT_SETTINGS, **current, **values}
    mode = str(merged.get("mode") or "manual").strip().lower()
    if mode not in AFFILIATE_MODES:
        raise ValueError("Affiliate mode phải là off, manual hoặc auto.")
    placement = str(merged.get("placement") or "first_comment").strip().lower()
    if placement not in AFFILIATE_PLACEMENTS:
        raise ValueError("Affiliate placement không hợp lệ.")
    min_commission = _fraction(merged.get("min_commission", merged.get("minCommission", 0.05)))
    settings = {
        "provider": "shopee",
        "enabled": bool(merged.get("enabled")) and mode != "off",
        "mode": mode,
        "placement": placement,
        "products_per_post": max(1, min(5, int(_float(merged.get("products_per_post", merged.get("productsPerPost", 1)), 1)))),
        "min_relevance": _fraction(merged.get("min_relevance", merged.get("minRelevance", 0.75))),
        "min_commission": min_commission,
    }
    return settings


def save_brand_settings(brand: str, values: dict | None = None) -> dict:
    brand = canonical_brand(brand)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
        raise ValueError("Brand không hợp lệ.")
    settings = normalize_settings(values, affiliate_store.get_settings(brand))
    return affiliate_store.upsert_settings(brand, settings)


def brand_context(config: dict | None, brand: str) -> dict:
    config = read_social_config() if config is None else config
    brand = canonical_brand(brand)
    status = shopee_status_for_brand(config, brand)
    settings = normalize_settings(status.get("settings"))
    pool_rows = affiliate_store.list_product_pool(brand, enabled_only=False, limit=200)
    return {
        "brand": brand,
        "provider": "shopee",
        "settings": settings,
        "pool": {
            "configured": bool(pool_rows),
            "total": len(pool_rows),
            "enabled": sum(1 for row in pool_rows if bool(row.get("enabled"))),
        },
        "connection": {
            "configured": bool(status.get("configured")),
            "connected": bool(status.get("connected")),
            "available": bool(status.get("available")),
            "app_id": str(status.get("app_id") or ""),
            "connection_id": str(status.get("connection_id") or ""),
            "display_name": str(status.get("display_name") or ""),
            "masked_secret": str(status.get("masked_secret") or ""),
            "api_base_url": str(status.get("api_base_url") or ""),
            "message": str(status.get("message") or ""),
        },
    }


def _valid_pool_affiliate_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and host in {*SHOPEE_HOSTS, "shp.today", "www.shp.today"} and bool(parsed.path)


def is_valid_affiliate_url(value: str) -> bool:
    """Accept Shopee links and the user's own HTTPS short-link domain."""
    return _valid_pool_affiliate_url(value)


def _pool_product_row(row: dict, query: str = "") -> dict:
    product = normalize_product(row, query)
    product.update({
        "id": str(row.get("id") or ""),
        "brand_id": str(row.get("brand_id") or ""),
        "affiliate_url": str(row.get("affiliate_url") or row.get("affiliateUrl") or "").strip(),
        "priority": int(_float(row.get("priority"), 0)),
        "enabled": bool(row.get("enabled", True)),
        "note": str(row.get("note") or "").strip(),
        "link_provider": "pool",
    })
    raw = product.get("raw") if isinstance(product.get("raw"), dict) else {}
    product["raw"] = {**raw, "_aurex_link_provider": "pool"}
    return product


def _authoritative_pool_product(
    brand: str,
    product_id: str,
    settings: dict | None = None,
    *,
    metadata: dict | None = None,
) -> dict:
    """Load one enabled Pool row again and discard all client-supplied URLs."""
    pool_id = str(product_id or "").strip()
    if not pool_id:
        return {}
    row = affiliate_store.get_product_pool(pool_id, brand_id=canonical_brand(brand))
    if not row or not bool(row.get("enabled")):
        return {}
    origin_candidate = str(row.get("origin_url") or "").strip()
    if origin_candidate:
        try:
            origin_url = validate_shopee_url(origin_candidate)
        except (TypeError, ValueError):
            return {}
    else:
        origin_url = ""
    affiliate_url = str(row.get("affiliate_url") or "").strip()
    if not _valid_pool_affiliate_url(affiliate_url):
        return {}
    commission_rate = _fraction(row.get("commission_rate"))
    minimum = _fraction((settings or {}).get("min_commission", (settings or {}).get("minCommission", 0.05)))
    if not math.isfinite(commission_rate) or commission_rate < 0 or (commission_rate > 0 and commission_rate < minimum):
        return {}
    product = _pool_product_row({**row, "origin_url": origin_url, "affiliate_url": affiliate_url})
    product["commission_rate"] = commission_rate
    if isinstance(metadata, dict):
        for key in ("relevance_score", "ranking_score", "_aurex_selection_mode", "_aurex_selection_reason"):
            if metadata.get(key) is not None:
                product[key] = metadata[key]
    return product


def select_pool_product(
    brand: str,
    query: str = "",
    *,
    settings: dict | None = None,
    selection_seed: object = "",
) -> dict:
    """Select one usable product only from the Brand's curated pool.

    A matching pool item wins on relevance when names are available. When the
    Pool contains links only, the deterministic fallback chooses one link so
    preview and execute keep the same item.
    """
    brand = canonical_brand(brand)
    settings = settings if isinstance(settings, dict) else affiliate_store.get_settings(brand)
    min_relevance = _fraction(settings.get("min_relevance", settings.get("minRelevance", 0.75)))
    min_commission = _fraction(settings.get("min_commission", settings.get("minCommission", 0.05)))
    rows = affiliate_store.list_product_pool(brand, enabled_only=True, limit=200)
    candidates: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        origin_url = str(row.get("origin_url") or "").strip()
        affiliate_url = str(row.get("affiliate_url") or "").strip()
        if origin_url:
            try:
                origin_url = validate_shopee_url(origin_url)
            except (TypeError, ValueError):
                continue
        if not _valid_pool_affiliate_url(affiliate_url):
            continue
        commission_rate = _fraction(row.get("commission_rate"))
        if not math.isfinite(commission_rate) or commission_rate < 0 or (commission_rate > 0 and commission_rate < min_commission):
            continue
        candidate = _pool_product_row({**row, "origin_url": origin_url, "affiliate_url": affiliate_url}, query)
        candidate["commission_rate"] = commission_rate
        candidates.append(candidate)
    if not candidates:
        return {}

    max_commission = max((product["commission_rate"] for product in candidates), default=0.0) or 1.0
    max_priority = max((max(0, int(product.get("priority") or 0)) for product in candidates), default=0)
    for product in candidates:
        commission_score = product["commission_rate"] / max_commission
        priority_score = (max(0, int(product.get("priority") or 0)) / max_priority) if max_priority else 0.0
        product["ranking_score"] = round(
            product["relevance_score"] * 0.55 + commission_score * 0.30 + priority_score * 0.15,
            6,
        )

    matching = [product for product in candidates if product["relevance_score"] >= min_relevance]
    if matching:
        selected = sorted(
            matching,
            key=lambda product: (
                -product["relevance_score"],
                -int(product.get("priority") or 0),
                -product["commission_rate"],
                -product["ranking_score"],
                product["name"].casefold(),
            ),
        )[0]
        selected["_aurex_selection_mode"] = "pool_match"
        selected["_aurex_selection_reason"] = "Đã chọn sản phẩm phù hợp từ Pool Shopee của Brand."
    else:
        ordered = sorted(
            candidates,
            key=lambda product: (
                -product["commission_rate"],
                -int(product.get("priority") or 0),
                -product["ranking_score"],
                product["name"].casefold(),
            ),
        )
        top_count = min(len(ordered), max(3, (len(ordered) + 3) // 4))
        finalists = ordered[:top_count]
        weights = [max(0.0001, product["commission_rate"]) ** 2 for product in finalists]
        seed = f"{brand}:{selection_seed or query}"
        chooser = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16))
        selected = finalists[-1]
        target = chooser.random() * sum(weights)
        for product, weight in zip(finalists, weights):
            target -= weight
            if target <= 0:
                selected = product
                break
        has_commission_data = any(_fraction(product.get("commission_rate")) > 0 for product in finalists)
        selected["_aurex_selection_mode"] = "pool_high_commission" if has_commission_data else "pool_random"
        selected["_aurex_selection_reason"] = (
            "Keyword chưa khớp; đã chọn trong Pool Shopee theo hoa hồng cao."
            if has_commission_data
            else "Keyword chưa khớp; đã chọn ngẫu nhiên trong Pool Shopee của Brand."
        )
        selected["ranking_score"] = round(selected["commission_rate"] / max_commission, 6)
    raw = selected.get("raw") if isinstance(selected.get("raw"), dict) else {}
    selected["raw"] = {
        **raw,
        "_aurex_link_provider": "pool",
        "_aurex_selection_mode": selected["_aurex_selection_mode"],
    }
    return dict(selected)


def _pool_bool(value: object, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


def save_product_pool(brand: str, values: dict | None = None) -> dict:
    """Validate and save one manually curated affiliate URL for a Brand."""
    brand = canonical_brand(brand)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
        raise ValueError("Brand không hợp lệ.")
    values = values if isinstance(values, dict) else {}
    name = str(values.get("name") or values.get("productName") or values.get("product_name") or "").strip()
    if len(name) > 300:
        raise ValueError("Tên sản phẩm trong Pool không được quá 300 ký tự.")
    origin_candidate = str(values.get("originUrl") or values.get("origin_url") or "").strip()
    if origin_candidate:
        try:
            origin_url = validate_shopee_url(origin_candidate)
        except (TypeError, ValueError) as exc:
            raise ValueError("Link gốc phải là link Shopee HTTPS hợp lệ.") from exc
    else:
        origin_url = ""
    affiliate_url = str(values.get("affiliateUrl") or values.get("affiliate_url") or "").strip()
    if len(affiliate_url) > 4000 or not _valid_pool_affiliate_url(affiliate_url):
        raise ValueError("Link rút gọn phải là HTTPS của Shopee hoặc shp.today.")
    commission_raw = values.get("commissionRate", values.get("commission_rate", 0))
    commission_rate = _fraction(commission_raw)
    if not math.isfinite(commission_rate):
        raise ValueError("Hoa hồng không hợp lệ.")
    priority = int(_float(values.get("priority"), 0))
    priority = max(-100, min(100, priority))
    pool_id = str(values.get("id") or values.get("poolId") or values.get("pool_id") or "").strip()
    if pool_id and len(pool_id) > 128:
        raise ValueError("Pool product ID không hợp lệ.")
    return affiliate_store.upsert_product_pool({
        "id": pool_id,
        "brand_id": brand,
        "provider": "shopee",
        "provider_product_id": str(values.get("providerProductId") or values.get("provider_product_id") or "").strip(),
        "shop_id": str(values.get("shopId") or values.get("shop_id") or "").strip(),
        "name": name,
        "origin_url": origin_url,
        "affiliate_url": affiliate_url,
        "commission_rate": commission_rate,
        "priority": priority,
        "enabled": _pool_bool(values.get("enabled"), True),
        "note": str(values.get("note") or "").strip()[:1000],
        "raw": {"source": "manual_pool"},
    })


def save_product_pool_links(brand: str, values: dict | None = None) -> dict:
    """Add a pasted newline-separated list of affiliate URLs to one Brand Pool."""
    values = values if isinstance(values, dict) else {}
    raw_links = values.get("links", values.get("affiliateUrls", values.get("affiliate_urls", values.get("linksText", ""))))
    if isinstance(raw_links, str):
        links = [line.strip() for line in raw_links.splitlines() if line.strip()]
    elif isinstance(raw_links, (list, tuple)):
        links = [str(line or "").strip() for line in raw_links if str(line or "").strip()]
    else:
        links = []
    links = list(dict.fromkeys(links))
    if not links:
        raise ValueError("Hãy dán ít nhất một link affiliate Shopee, mỗi link một dòng.")
    if len(links) > 200:
        raise ValueError("Mỗi lần chỉ thêm tối đa 200 link vào Pool.")
    invalid = next((link for link in links if len(link) > 4000 or not _valid_pool_affiliate_url(link)), "")
    if invalid:
        raise ValueError(f"Link affiliate không hợp lệ: {invalid}")
    if values.get("id") or values.get("poolId") or values.get("pool_id"):
        raise ValueError("Khi sửa Pool, chỉ nhập một link trong form sửa.")
    common = {
        key: value
        for key, value in values.items()
        if key not in {"links", "affiliateUrls", "affiliate_urls", "linksText", "id", "poolId", "pool_id"}
    }
    products = [save_product_pool(brand, {**common, "affiliateUrl": link}) for link in links]
    return {"ok": True, "brand": canonical_brand(brand), "count": len(products), "products": products}


def list_product_pool(brand: str, *, query: str = "", enabled_only: bool = False, limit: int = 100) -> dict:
    brand = canonical_brand(brand)
    rows = affiliate_store.list_product_pool(brand, query=query, enabled_only=enabled_only, limit=limit)
    return {
        "ok": True,
        "brand": brand,
        "products": rows,
        "items": rows,
        "configured": bool(rows),
        "count": len(rows),
    }


def delete_product_pool(brand: str, pool_id: str) -> dict:
    brand = canonical_brand(brand)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
        raise ValueError("Brand không hợp lệ.")
    if not str(pool_id or "").strip():
        raise ValueError("Cần chỉ định sản phẩm cần xoá khỏi Pool.")
    return affiliate_store.delete_product_pool(str(pool_id).strip(), brand_id=brand)


def _project_query(project: str) -> str:
    from .metadata import read_script_lines, require_project

    project_dir = require_project(project)
    lines = read_script_lines(project_dir)
    if lines:
        return " ".join(lines[:4])[:500]
    return project_dir.name.replace("-", " ")


def discover_products(brand: str, query: str, *, limit: int = 10, persist: bool = True) -> dict:
    config = read_social_config()
    brand = canonical_brand(brand)
    context = brand_context(config, brand)
    if not context["connection"]["connected"]:
        raise ValueError(context["connection"]["message"] or "Shopee Affiliate chưa kết nối cho Brand.")
    _, connection = resolve_social_brand_connection(config, brand, "shopee")
    raw_products = search_product_offers(connection, query, limit=limit)
    historical_rates = affiliate_store.product_conversion_rates("shopee")
    enriched_products = []
    for raw_product in raw_products:
        if not isinstance(raw_product, dict):
            continue
        provider_id = str(
            raw_product.get("provider_product_id")
            or raw_product.get("productId")
            or raw_product.get("itemId")
            or raw_product.get("item_id")
            or ""
        ).strip()
        origin_url = str(raw_product.get("origin_url") or raw_product.get("productLink") or raw_product.get("product_link") or "").strip()
        if "historical_conversion" not in raw_product and "historicalConversion" not in raw_product:
            raw_product = {
                **raw_product,
                "historical_conversion": historical_rates.get(provider_id) or historical_rates.get(origin_url) or 0.0,
            }
        enriched_products.append(raw_product)
    ranked = rank_products(enriched_products, query)
    settings = context["settings"]
    filtered = [
        product for product in ranked
        if product["relevance_score"] >= settings["min_relevance"]
        and product["commission_rate"] >= settings["min_commission"]
    ]
    if not persist:
        return {"brand": brand, "query": query, "products": filtered, "candidates": len(ranked), "settings": settings}
    stored = []
    for product in filtered:
        saved = affiliate_store.upsert_product(product)
        saved.update({"relevance_score": product["relevance_score"], "ranking_score": product["ranking_score"]})
        stored.append(saved)
    return {"brand": brand, "query": query, "products": stored, "candidates": len(ranked), "settings": settings}


def list_saved_products(query: str = "", *, limit: int = 50) -> dict:
    """Return cached products for the dashboard without calling Shopee."""
    products = affiliate_store.list_products(query=query, limit=limit)
    return {"query": str(query or "").strip(), "products": products, "candidates": len(products)}


def _valid_shopee_link(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme == "https" and (parsed.hostname or "").lower().rstrip(".") in SHOPEE_HOSTS and bool(parsed.path)


def create_affiliate_link(
    *,
    brand: str,
    content_id: str,
    product_id: str = "",
    origin_url: str = "",
    affiliate_url: str = "",
    placement: str = "first_comment",
    page_id: str = "",
    product_payload: dict | None = None,
    link_provider: str = "",
    reuse_product_offer_url: bool = True,
) -> dict:
    config = read_social_config()
    brand = canonical_brand(brand)
    content_id = str(content_id or "").strip()
    placement = str(placement or "first_comment").strip().lower()
    link_provider = str(link_provider or "").strip().lower()
    if not brand or not content_id:
        raise ValueError("Affiliate link cần Brand và content/project id.")
    if placement not in AFFILIATE_PLACEMENTS:
        raise ValueError("Affiliate placement không hợp lệ.")
    product = affiliate_store.get_product(product_id) if product_id else {}
    origin_url = str(origin_url or product.get("origin_url") or "").strip()
    affiliate_url = str(affiliate_url or "").strip()
    if isinstance(product_payload, dict):
        payload_provider = product_payload.get("link_provider") or product_payload.get("linkProvider")
        payload_raw = product_payload.get("raw")
        if isinstance(payload_raw, dict):
            payload_provider = payload_provider or payload_raw.get("_aurex_link_provider")
        if not link_provider:
            link_provider = str(payload_provider or "").strip().lower()
        if not product_id:
            product_id = str(product_payload.get("id") or product_payload.get("pool_id") or "").strip()
        if link_provider == "pool":
            if not product_id:
                raise ValueError("Cần chọn sản phẩm có ID trong Pool Shopee của Brand.")
            pool_row = affiliate_store.get_product_pool(product_id, brand_id=brand)
            if not pool_row:
                raise ValueError("Sản phẩm Pool không thuộc Brand này hoặc đã bị xoá.")
            if not bool(pool_row.get("enabled")):
                raise ValueError("Sản phẩm Pool đang tắt; hãy bật lại trước khi dùng.")
            product = _pool_product_row(pool_row)
            origin_url = str(product.get("origin_url") or "").strip()
            affiliate_url = str(product.get("affiliate_url") or "").strip()
        origin_url = str(
            origin_url
            or product_payload.get("origin_url")
            or product_payload.get("original_url")
            or product_payload.get("productLink")
            or product_payload.get("product_link")
            or ""
        ).strip()
        if reuse_product_offer_url and not affiliate_url:
            affiliate_url = str(
                product_payload.get("affiliate_url")
                or product_payload.get("affiliateUrl")
                or product_payload.get("shortUrl")
                or ""
            ).strip()
        if not product:
            candidate = normalize_product(product_payload)
            candidate_id = str(product_payload.get("id") or product_id or "").strip()
            if candidate_id:
                candidate["id"] = candidate_id
            if link_provider in {"shopee", "pool"}:
                candidate["link_provider"] = link_provider
            if affiliate_url:
                candidate["affiliate_url"] = affiliate_url
            if candidate.get("origin_url") or candidate.get("offer_url"):
                product = candidate if link_provider == "pool" else affiliate_store.upsert_product(candidate)
                product_id = str(product.get("id") or product_id)
                origin_url = str(origin_url or product.get("origin_url") or "").strip()
    if link_provider not in {"", "shopee", "pool"}:
        link_provider = ""
    raw_product = product.get("raw") if isinstance(product.get("raw"), dict) else {}
    stored_link_provider = str(
        raw_product.get("_aurex_link_provider")
        or product.get("link_provider")
        or ""
    ).strip().lower()
    if stored_link_provider not in {"shopee", "pool"}:
        stored_link_provider = ""
    if stored_link_provider and link_provider and stored_link_provider != link_provider:
        raise ValueError("Link provider không khớp provenance của sản phẩm.")
    link_provider = stored_link_provider or link_provider
    if not link_provider and affiliate_url and not _valid_shopee_link(affiliate_url):
        if _valid_pool_affiliate_url(affiliate_url):
            link_provider = "pool"
    if reuse_product_offer_url and not affiliate_url:
        affiliate_url = str(
            (product.get("affiliate_url") if link_provider == "pool" else product.get("offer_url")) or ""
        ).strip()
    if link_provider == "pool":
        if not product_id:
            raise ValueError("Cần chọn sản phẩm có ID trong Pool Shopee của Brand.")
        pool_row = affiliate_store.get_product_pool(product_id, brand_id=brand)
        if not pool_row:
            raise ValueError("Sản phẩm Pool không thuộc Brand này hoặc đã bị xoá.")
        if not bool(pool_row.get("enabled")):
            raise ValueError("Sản phẩm Pool đang tắt; hãy bật lại trước khi dùng.")
        product = _pool_product_row(pool_row)
        origin_url = str(product.get("origin_url") or "").strip()
        affiliate_url = str(product.get("affiliate_url") or "").strip()
    if not origin_url and not affiliate_url:
        raise ValueError("Cần product hoặc Shopee origin URL để tạo affiliate link.")
    if origin_url and not _valid_shopee_link(origin_url):
        raise ValueError("Origin URL phải là link Shopee HTTPS hợp lệ.")
    if affiliate_url and not _valid_pool_affiliate_url(affiliate_url):
        raise ValueError("Affiliate URL phải là HTTPS của Shopee hoặc shp.today.")
    if link_provider == "pool" and not affiliate_url:
        raise ValueError("Sản phẩm trong Pool cần có link affiliate đã rút gọn.")
    if not page_id:
        brand_routes = config.get("brand_routes") if isinstance(config, dict) else {}
        brand_route = brand_routes.get(brand) if isinstance(brand_routes, dict) else {}
        facebook_route = brand_route.get("facebook") if isinstance(brand_route, dict) else {}
        if isinstance(facebook_route, dict):
            page_id = str(facebook_route.get("page_id") or facebook_route.get("connection_id") or "").strip()
    sub_ids = build_sub_ids(brand, page_id, content_id, product_id or product.get("provider_product_id"), placement)
    if not affiliate_url:
        _, connection = resolve_social_brand_connection(config, brand, "shopee")
        affiliate_url = generate_short_link(connection, origin_url, sub_ids)
    link = affiliate_store.record_link({
        "content_id": content_id,
        "brand_id": brand,
        "provider": "shopee",
        "product_id": product.get("id") or product_id,
        "origin_url": origin_url,
        "affiliate_url": affiliate_url,
        "placement": placement,
        "status": "created",
        **{f"sub_id_{index}": value for index, value in enumerate(sub_ids, 1)},
    })
    return {"ok": True, "link": link, "product": product, "sub_ids": sub_ids}


def prepare_affiliate_for_publish(payload: dict, project: str, brand: str, page_id: str = "") -> dict:
    config = read_social_config()
    has_affiliate_payload = isinstance(payload.get("affiliate"), dict)
    raw = payload.get("affiliate") if has_affiliate_payload else {}
    brand = canonical_brand(brand)
    settings = affiliate_store.get_settings(brand)
    mode = str(raw.get("mode") or raw.get("affiliateMode") or "").strip().lower()
    if not mode:
        mode = str(settings.get("mode") or "off").strip().lower() if (not has_affiliate_payload or raw.get("enabled")) else "off"
    enabled = bool(raw.get("enabled", settings.get("enabled") if not has_affiliate_payload else mode != "off")) and mode != "off"
    if not enabled:
        return {"enabled": False, "mode": "off", "placement": "first_comment", "auto_comment": False}
    if mode not in AFFILIATE_MODES:
        raise ValueError("Affiliate mode phải là off, manual hoặc auto.")
    placement = str(raw.get("placement") or settings.get("placement") or "first_comment").strip().lower()
    if placement not in AFFILIATE_PLACEMENTS:
        raise ValueError("Affiliate placement không hợp lệ.")
    if placement == "shopee_native_tag":
        raise ValueError("Shopee native tag mới là POC; hãy dùng comment hoặc caption cho lần đăng này.")
    query = str(raw.get("query") or raw.get("affiliateQuery") or "").strip() or _project_query(project)
    product_id = str(raw.get("productId") or raw.get("product_id") or raw.get("affiliateProductId") or "").strip()
    product_payload = raw.get("product") if isinstance(raw.get("product"), dict) else None
    # AUTO never reads the global provider catalog.  It is deliberately
    # resolved from the current Brand-owned Pool below.
    product = affiliate_store.get_product(product_id) if product_id and mode != "auto" else {}
    origin_url = str(raw.get("originUrl") or raw.get("origin_url") or "").strip() or str(product.get("origin_url") or "")
    if product_payload and (mode == "auto" or not product):
        product = dict(product_payload)
        if not product_id:
            product_id = str(product.get("id") or product.get("pool_id") or "").strip()
        if not origin_url:
            origin_url = str(product.get("origin_url") or product.get("original_url") or "").strip()
    link_provider = str(raw.get("linkProvider") or raw.get("link_provider") or "").strip().lower()
    if not link_provider and product:
        product_raw = product.get("raw") if isinstance(product.get("raw"), dict) else {}
        link_provider = str(
            product.get("link_provider")
            or product_raw.get("_aurex_link_provider")
            or ""
        ).strip().lower()
    if link_provider not in {"", "shopee", "pool"}:
        link_provider = ""
    affiliate_url = str(raw.get("affiliateUrl") or raw.get("affiliate_url") or "").strip()
    if not affiliate_url:
        affiliate_url = str(
            (product.get("affiliate_url") if link_provider == "pool" else product.get("offer_url")) or ""
        ).strip()
    ranking_score = _float(raw.get("rankingScore"))
    relevance_score = _float(raw.get("relevanceScore"))
    if mode == "auto":
        # A client may send stale/manual provider fields.  They are hints only;
        # the exact Pool row is reloaded by Brand and supplies both URLs.
        product = _authoritative_pool_product(
            brand,
            product_id,
            settings,
            metadata=product_payload,
        )
        if not product:
            product = select_pool_product(brand, query, settings=settings, selection_seed=project)
        if not product:
            raise ValueError("Pool Shopee không có link affiliate hợp lệ đang bật cho Brand.")
        product_id = str(product.get("id") or "").strip()
        origin_url = str(product.get("origin_url") or "").strip()
        affiliate_url = str(product.get("affiliate_url") or "").strip()
        link_provider = "pool"
        ranking_score = _float(product.get("ranking_score"))
        relevance_score = _float(product.get("relevance_score"))
    if not product_id and product:
        product_id = str(product.get("id") or "").strip()
    if not origin_url and product:
        origin_url = str(product.get("origin_url") or "").strip()
    if not affiliate_url and not origin_url:
        raise ValueError("Chưa có sản phẩm Shopee để tạo affiliate link.")
    link_result = create_affiliate_link(
        brand=brand,
        content_id=project,
        product_id=product_id,
        origin_url=origin_url,
        affiliate_url=affiliate_url,
        placement=placement,
        page_id=page_id,
        product_payload=product if product else None,
        link_provider=link_provider,
    )
    link_row = link_result["link"]
    record = affiliate_store.record_content_product({
        "content_id": project,
        "brand_id": brand,
        "provider": "shopee",
        "product_id": str(product.get("id") or product_id),
        "product_name": str(product.get("name") or "Shopee product"),
        "original_url": origin_url,
        "affiliate_url": str(link_row.get("affiliate_url") or ""),
        "commission_rate": _fraction(product.get("commission_rate")),
        "relevance_score": relevance_score,
        "ranking_score": ranking_score,
        "placement": placement,
        "page_id": page_id,
        "status": "prepared",
        **{f"sub_id_{index}": value for index, value in enumerate(link_result.get("sub_ids") or [], 1)},
    })
    job = affiliate_store.record_publish_job({
        "content_id": project,
        "brand_id": brand,
        "provider": "shopee",
        "link_id": str(link_row.get("id") or ""),
        "platform": "facebook",
        "page_id": page_id,
        "placement": placement,
        "status": "prepared",
    })
    auto_comment = bool(raw.get("autoComment", raw.get("auto_comment", placement in {"first_comment", "caption_and_comment"})))
    return {
        "enabled": True,
        "mode": mode,
        "placement": placement,
        "auto_comment": auto_comment,
        "query": query,
        "product": product,
        "link_provider": link_provider,
        "link": link_row,
        "record_id": str(record.get("id") or ""),
        "job_id": str(job.get("id") or ""),
    }


class _ProductTitleParser(HTMLParser):
    """Extract a short product title without depending on a HTML package."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta_title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta":
            marker = normalized.get("property", "").lower() or normalized.get("name", "").lower()
            if marker in {"og:title", "twitter:title"} and normalized.get("content"):
                self.meta_title = normalized["content"]

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data)

    def value(self) -> str:
        return self.meta_title or " ".join(self.title_parts)


def _clean_comment_title(value: object) -> str:
    title = " ".join(str(value or "").replace("\u0000", "").split()).strip()
    title = re.sub(r"\s*[|–—-]\s*(?:Shopee|Shopee Việt Nam).*$", "", title, flags=re.IGNORECASE).strip(" -|–—")
    if not title or title.casefold() in {"shopee", "shopee việt nam", "shopee vietnam", "shopee product", "shopee pool link"}:
        return ""
    return title[:140].rstrip()


@lru_cache(maxsize=256)
def _fetch_product_title(url: str) -> str:
    """Read a title from a validated Shopee URL with a small timeout."""
    try:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AurexVideo/1.0",
            },
            method="GET",
        )
        with urlopen(request, timeout=8) as response:
            final_url = str(response.geturl() or url)
            final_host = (urlparse(final_url).hostname or "").lower().rstrip(".")
            if final_host not in {*SHOPEE_HOSTS, "shp.today", "www.shp.today"}:
                return ""
            content = response.read(512 * 1024).decode("utf-8", "replace")
        parser = _ProductTitleParser()
        parser.feed(content)
        return _clean_comment_title(parser.value())
    except (OSError, UnicodeError, ValueError):
        return ""


def product_title_for_comment(product: dict | None = None) -> str:
    """Return a concise Pool/product title, resolving it from the link if needed."""
    product = product if isinstance(product, dict) else {}
    title = _clean_comment_title(product.get("name"))
    if title:
        return title
    for key in ("origin_url", "affiliate_url"):
        candidate = str(product.get(key) or "").strip()
        if not candidate:
            continue
        host = (urlparse(candidate).hostname or "").lower().rstrip(".")
        if host not in {*SHOPEE_HOSTS, "shp.today", "www.shp.today"}:
            continue
        title = _fetch_product_title(candidate)
        if title:
            return title
    return ""


def affiliate_comment_text(
    affiliate_url: str,
    *,
    product_name: str = "",
    fallback: bool = False,
) -> str:
    """Return a clickable plain-text affiliate comment.

    Keep the tracking URL in ``message``.  Facebook Page comment routes can
    reject the optional ``attachment_url`` field even when the Page token can
    read and comment on the post.
    """
    label = "Gợi ý trên Shopee" if fallback else "Sản phẩm liên quan"
    title = _clean_comment_title(product_name)
    if title:
        return f"🛒 {label}: {title}\n{affiliate_url}"
    return f"🛒 {label} trong video:\n{affiliate_url}"


def caption_with_affiliate(caption: str, affiliate_url: str) -> str:
    line = f"🛒 Sản phẩm liên quan: {affiliate_url}"
    caption = str(caption or "").strip()
    return caption if affiliate_url in caption else f"{caption}\n\n{line}".strip()


def finalize_affiliate_publish(
    prepared: dict,
    *,
    page_id: str,
    post_id: str,
    comment_id: str = "",
    error: str = "",
    status: str = "published",
) -> dict:
    if not prepared or not prepared.get("enabled"):
        return {}
    record_id = str(prepared.get("record_id") or "")
    job_id = str(prepared.get("job_id") or "")
    if record_id:
        affiliate_store.update_content_product(
            record_id,
            page_id=page_id,
            facebook_post_id=post_id,
            facebook_comment_id=comment_id,
            status=status,
            error=error,
        )
    if job_id:
        affiliate_store.update_publish_job(
            job_id,
            page_id=page_id,
            post_id=post_id,
            comment_id=comment_id,
            status=status,
            error=error,
        )
    return {
        "enabled": True,
        "mode": prepared.get("mode"),
        "placement": prepared.get("placement"),
        "auto_comment": bool(prepared.get("auto_comment")),
        "product": prepared.get("product") or {},
        "link": prepared.get("link") or {},
        "comment_id": comment_id,
        "status": status,
        "error": error,
    }


def overview(brand: str = "", content_id: str = "", *, start_date: str = "", end_date: str = "") -> dict:
    brand = canonical_brand(brand)
    result = affiliate_store.overview(
        brand_id=brand,
        content_id=str(content_id or "").strip(),
        start_date=str(start_date or "").strip(),
        end_date=str(end_date or "").strip(),
    )
    result["brand"] = brand
    result["recent_conversions"] = affiliate_store.list_recent_conversions(brand_id=brand)
    result["connection"] = brand_context(read_social_config(), brand)["connection"] if brand else {}
    return result


def ingest_conversion_rows(rows: list[dict], brand: str = "") -> dict:
    """Persist normalized rows supplied by a future Shopee report sync job."""
    count = 0
    affected_stats: list[tuple[str, str, str, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        normalized["brand_id"] = canonical_brand(normalized.get("brand_id") or brand)
        affiliate_store.record_conversion(normalized)
        order_time = str(normalized.get("order_time") or normalized.get("click_time") or "")
        stat_date = order_time[:10] if len(order_time) >= 10 else datetime.now(timezone.utc).date().isoformat()
        affected_stats.append((
            stat_date,
            normalized["brand_id"],
            str(normalized.get("content_id") or ""),
            str(normalized.get("product_id") or ""),
        ))
        count += 1
    affiliate_store.rebuild_daily_stats_from_conversions(affected_stats)
    return {"ok": True, "imported": count}
