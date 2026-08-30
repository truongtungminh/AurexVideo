from __future__ import annotations

"""Provider-neutral orchestration for AurexVideo affiliate publishing."""

import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import affiliate_store
from .config import canonical_brand, read_social_config, resolve_social_brand_connection
from .shopee import (
    SHOPEE_HOSTS,
    generate_short_link,
    search_product_offers,
    shopee_status_for_brand,
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
    provider_product_id = str(raw.get("provider_product_id") or raw.get("itemId") or raw.get("item_id") or "").strip()
    origin_url = str(
        raw.get("origin_url")
        or raw.get("original_url")
        or raw.get("productLink")
        or raw.get("product_link")
        or raw.get("productUrl")
        or raw.get("product_url")
        or raw.get("offerLink")
        or raw.get("offer_url")
        or ""
    ).strip()
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
        "shop_id": str(raw.get("shop_id") or raw.get("shopId") or "").strip(),
        "name": name or "Shopee product",
        "origin_url": origin_url,
        "offer_url": str(raw.get("offer_url") or raw.get("offerLink") or "").strip(),
        "image_url": str(raw.get("image_url") or raw.get("imageUrl") or "").strip(),
        "price_min": _float(raw.get("price_min", raw.get("priceMin"))),
        "price_max": _float(raw.get("price_max", raw.get("priceMax"))),
        "commission_rate": _fraction(raw.get("commission_rate", raw.get("commissionRate"))),
        "sales": max(0.0, _float(raw.get("sales"))),
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
    return {
        "brand": brand,
        "provider": "shopee",
        "settings": settings,
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


def _project_query(project: str) -> str:
    from .metadata import read_script_lines, require_project

    project_dir = require_project(project)
    lines = read_script_lines(project_dir)
    if lines:
        return " ".join(lines[:4])[:500]
    return project_dir.name.replace("-", " ")


def discover_products(brand: str, query: str, *, limit: int = 10) -> dict:
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
        provider_id = str(raw_product.get("provider_product_id") or raw_product.get("itemId") or raw_product.get("item_id") or "").strip()
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
) -> dict:
    config = read_social_config()
    brand = canonical_brand(brand)
    content_id = str(content_id or "").strip()
    placement = str(placement or "first_comment").strip().lower()
    if not brand or not content_id:
        raise ValueError("Affiliate link cần Brand và content/project id.")
    if placement not in AFFILIATE_PLACEMENTS:
        raise ValueError("Affiliate placement không hợp lệ.")
    product = affiliate_store.get_product(product_id) if product_id else {}
    origin_url = str(origin_url or product.get("origin_url") or "").strip()
    affiliate_url = str(affiliate_url or "").strip()
    if not product and isinstance(product_payload, dict):
        candidate = normalize_product(product_payload)
        if candidate.get("origin_url") or candidate.get("offer_url"):
            product = affiliate_store.upsert_product(candidate)
            product_id = str(product.get("id") or product_id)
            origin_url = str(origin_url or product.get("origin_url") or "").strip()
    if isinstance(product_payload, dict):
        origin_url = str(
            origin_url
            or product_payload.get("origin_url")
            or product_payload.get("original_url")
            or product_payload.get("productLink")
            or product_payload.get("product_link")
            or ""
        ).strip()
        affiliate_url = str(
            affiliate_url
            or product_payload.get("affiliate_url")
            or product_payload.get("affiliateUrl")
            or product_payload.get("shortUrl")
            or ""
        ).strip()
    if not origin_url and not affiliate_url:
        raise ValueError("Cần product hoặc Shopee origin URL để tạo affiliate link.")
    if origin_url and not _valid_shopee_link(origin_url):
        raise ValueError("Origin URL phải là link Shopee HTTPS hợp lệ.")
    if affiliate_url and not _valid_shopee_link(affiliate_url):
        raise ValueError("Affiliate URL phải là link Shopee HTTPS hợp lệ.")
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
    product = affiliate_store.get_product(product_id) if product_id else {}
    origin_url = str(raw.get("originUrl") or raw.get("origin_url") or "").strip() or str(product.get("origin_url") or "")
    affiliate_url = str(raw.get("affiliateUrl") or raw.get("affiliate_url") or "").strip()
    ranking_score = _float(raw.get("rankingScore"))
    relevance_score = _float(raw.get("relevanceScore"))
    if mode == "auto" and not product and not affiliate_url:
        discovery = discover_products(brand, query, limit=max(5, int(_float(settings.get("products_per_post"), 1))))
        product = (discovery.get("products") or [None])[0] or {}
        product_id = str(product.get("id") or "").strip()
        origin_url = str(product.get("origin_url") or "").strip()
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
        "link": link_row,
        "record_id": str(record.get("id") or ""),
        "job_id": str(job.get("id") or ""),
    }


def affiliate_comment_text(affiliate_url: str) -> str:
    return f"🛒 Sản phẩm liên quan trong video:\n{affiliate_url}"


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
