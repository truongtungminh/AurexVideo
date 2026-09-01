from __future__ import annotations

"""Conservative, Brand-scoped affiliate comment backfill for Facebook Pages.

This module is intentionally not wired to an HTTP route.  Its public entry
point is suitable for an explicit operator action and defaults to dry-run.
"""

import hashlib
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import quote, urlsplit, urlunsplit

from . import affiliate_store
from .addlivetag import AddLiveTagApiError, extract_shopee_reference, fetch_product_data, normalize_product_payload
from .affiliate import (
    affiliate_comment_text,
    brand_context,
    create_affiliate_link,
    discover_products,
    rank_products,
)
from .config import canonical_brand, read_social_config
from .facebook import (
    facebook_config,
    facebook_full_post_id,
    facebook_graph_version,
    facebook_page_access_token,
    facebook_upload_page,
    post_facebook_source_comment,
)
from .http import http_get_request


MAX_LIMIT = 50
MAX_LOOKBACK_DAYS = 365
MAX_POST_PAGES = 5
MAX_COMMENT_PAGES = 10
POST_PREVIEW_LENGTH = 500
_BACKFILL_LOCK = threading.RLock()
_SHOPEE_URL_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)?(?:shopee\.(?:vn|co\.id|co\.th|ph|sg|com\.my)|s\.shopee\.(?:vn|co\.id|co\.th|ph|sg|com\.my))[^\s<>'\"]*", re.I)
_COMMENT_MARKER = "sản phẩm liên quan"
_SECRET_RE = re.compile(r"(?i)(access[_-]?token|authorization|secret|affiliate[_-]?id)\s*(?:=|:|%3d)[^\s&,'\"]+")


def _fraction(value: object) -> float:
    try:
        number = float(str(value or "0").strip().replace("%", ""))
    except (TypeError, ValueError):
        return 0.0
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def _safe_error(error: object) -> str:
    """Return provider context without leaking query-string credentials."""
    text = _SECRET_RE.sub(r"\1=[redacted]", str(error or "Provider request failed."))
    try:
        match = re.search(r"https?://[^\s]+", text)
        if match:
            parsed = urlsplit(match.group(0))
            text = text[:match.start()] + urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) + text[match.end():]
    except ValueError:
        pass
    return text.strip()[:300] or "Provider request failed."


def _parse_created_time(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # Graph commonly returns offsets without a colon, e.g. ``+0000``.
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_published(value: object) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() not in {"0", "false", "no", "draft", "unpublished"}


def _post_preview(value: object) -> str:
    return " ".join(str(value or "").split())[:POST_PREVIEW_LENGTH]


def _normalize_posts(rows: Iterable[object], *, page_id: str, cutoff: datetime) -> list[dict]:
    """Keep only recent published Graph objects and dedupe feed/reels overlaps."""
    posts: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        post_id = str(row.get("id") or "").strip()
        created_time = _parse_created_time(row.get("created_time"))
        if not post_id or not created_time or created_time < cutoff or not _is_published(row.get("is_published")):
            continue
        message = _post_preview(row.get("message"))
        description = _post_preview(row.get("description"))
        normalized = {
            "id": post_id,
            "post_id": post_id,
            "message_preview": message,
            "description_preview": description,
            "created_time": created_time.isoformat(),
            "permalink_url": str(row.get("permalink_url") or "").strip(),
            "page_id": page_id,
            "_text": "\n".join(part for part in (message, description) if part).strip(),
        }
        existing = posts.get(post_id)
        if not existing or len(normalized["_text"]) > len(existing["_text"]):
            posts[post_id] = normalized
    return sorted(posts.values(), key=lambda post: post["created_time"], reverse=True)


def _safe_post(post: dict) -> dict:
    return {
        key: post.get(key, "")
        for key in ("id", "post_id", "message_preview", "description_preview", "created_time", "permalink_url", "page_id")
    }


def _graph_data(url: str, fields: dict) -> dict:
    data = http_get_request(url, fields)
    if not isinstance(data, dict):
        raise RuntimeError("Facebook Graph API returned invalid data.")
    error = data.get("error")
    if isinstance(error, dict):
        raise RuntimeError(str(error.get("message") or "Facebook Graph API request failed."))
    return data


def _next_graph_page_url(value: object) -> str:
    """Accept only Graph-owned HTTPS paging URLs; never expose their query string."""
    next_url = str(value or "").strip()
    if not next_url:
        return ""
    try:
        parsed = urlsplit(next_url)
    except ValueError as exc:
        raise RuntimeError("Facebook Graph returned an invalid paging URL.") from exc
    if parsed.scheme != "https" or parsed.hostname != "graph.facebook.com":
        raise RuntimeError("Facebook Graph returned an unsafe paging URL.")
    return next_url


def _read_graph_edge(url: str, fields: dict, edge: str) -> list[object]:
    """Read one Graph edge through a small, fail-safe pagination budget."""
    rows: list[object] = []
    current_url = url
    for page_number in range(MAX_POST_PAGES):
        data = _graph_data(current_url, fields if page_number == 0 else {})
        page_rows = data.get("data")
        if not isinstance(page_rows, list):
            raise RuntimeError(f"Facebook {edge} response did not include data.")
        rows.extend(page_rows)
        paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
        next_url = _next_graph_page_url(paging.get("next"))
        if not next_url:
            return rows
        current_url = next_url
    raise RuntimeError(f"Facebook {edge} paging limit reached; narrow the backfill window and retry.")


def _read_page_posts(facebook: dict, page: dict, *, limit: int, cutoff: datetime) -> list[dict]:
    page_id = str(page.get("id") or "").strip()
    access_token = facebook_page_access_token(facebook, page)
    if not page_id or not access_token:
        raise ValueError("Facebook Page chưa có Page access token hợp lệ.")
    base = f"https://graph.facebook.com/{facebook_graph_version(facebook)}/{quote(page_id, safe='')}"
    fields = {
        "fields": "id,message,description,created_time,permalink_url,is_published",
        "limit": str(min(MAX_LIMIT, max(limit, 20))),
        "access_token": access_token,
    }
    rows: list[object] = []
    for edge in ("feed", "video_reels"):
        rows.extend(_read_graph_edge(f"{base}/{edge}", fields, edge))
    return _normalize_posts(rows, page_id=page_id, cutoff=cutoff)[:limit]


def _has_existing_affiliate_comment(comments: Iterable[object]) -> bool:
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("message") or "")
        lowered = text.casefold()
        if _COMMENT_MARKER in lowered or _SHOPEE_URL_RE.search(text):
            return True
    return False


def _read_comments(facebook: dict, page: dict, post_id: str) -> tuple[bool, str]:
    """Inspect every bounded Graph comments page; incomplete inspection is unsafe."""
    access_token = facebook_page_access_token(facebook, page)
    target_id = facebook_full_post_id(facebook, post_id, page)
    url = f"https://graph.facebook.com/{facebook_graph_version(facebook)}/{quote(target_id, safe='')}/comments"
    fields = {
        "fields": "id,message,created_time",
        "limit": "100",
        "filter": "toplevel",
        "access_token": access_token,
    }
    comments: list[object] = []
    for page_number in range(MAX_COMMENT_PAGES):
        data = _graph_data(url, fields if page_number == 0 else {})
        rows = data.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Facebook comments response did not include data.")
        comments.extend(rows)
        if _has_existing_affiliate_comment(rows):
            return True, ""
        paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
        next_url = _next_graph_page_url(paging.get("next"))
        if not next_url:
            return False, ""
        url = next_url
    raise RuntimeError("Facebook comments pagination limit reached; cannot safely verify duplicates.")


def _content_id(brand: str, page_id: str, post_id: str) -> str:
    digest = hashlib.sha256(f"{brand}:{page_id}:{post_id}".encode("utf-8")).hexdigest()[:24]
    return f"facebook-backfill-{digest}"


def _local_successful_record(brand: str, content_id: str, page_id: str, post_id: str) -> dict:
    records = affiliate_store.overview(brand_id=brand, content_id=content_id, limit=50).get("products") or []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("page_id") or "") != page_id or str(record.get("facebook_post_id") or "") != post_id:
            continue
        if str(record.get("facebook_comment_id") or "").strip() and str(record.get("status") or "").casefold() in {
            "commented", "published", "completed", "success",
        }:
            return record
    return {}


def _existing_record(brand: str, content_id: str, page_id: str, post_id: str) -> dict:
    records = affiliate_store.overview(brand_id=brand, content_id=content_id, limit=50).get("products") or []
    for record in records:
        if isinstance(record, dict) and str(record.get("page_id") or "") == page_id and str(record.get("facebook_post_id") or "") == post_id:
            return record
    return {}


def _product_summary(product: dict) -> dict:
    return {
        "id": str(product.get("id") or ""),
        "provider": str(product.get("link_provider") or product.get("provider") or "shopee"),
        "name": str(product.get("name") or "")[:200],
        "commission_rate": _fraction(product.get("commission_rate")),
        "relevance_score": _fraction(product.get("relevance_score")),
        "ranking_score": round(float(product.get("ranking_score") or 0), 6),
    }


def _policy_product(products: Iterable[object], query: str, settings: dict) -> dict:
    min_relevance = _fraction(settings.get("min_relevance", settings.get("minRelevance", 0.75)))
    min_commission = _fraction(settings.get("min_commission", settings.get("minCommission", 0.05)))
    source_products = [product for product in products if isinstance(product, dict)]
    source_by_key = {
        (
            str(product.get("provider_product_id") or product.get("itemId") or product.get("item_id") or ""),
            str(product.get("origin_url") or product.get("original_url") or product.get("productLink") or product.get("product_url") or ""),
        ): product
        for product in source_products
    }
    ranked = rank_products(source_products, query)
    acceptable = [
        product for product in ranked
        if _fraction(product.get("relevance_score")) >= min_relevance
        and _fraction(product.get("commission_rate")) >= min_commission
        and str(product.get("origin_url") or "").strip()
    ]
    if not acceptable:
        return {}
    selected = sorted(acceptable, key=lambda product: (-float(product.get("ranking_score") or 0), -_fraction(product.get("commission_rate"))))[0]
    source = source_by_key.get((str(selected.get("provider_product_id") or ""), str(selected.get("origin_url") or "")), {})
    for key in ("id", "link_provider"):
        if source.get(key):
            selected[key] = source[key]
    return selected


def _select_product(brand: str, text: str, context: dict) -> tuple[dict, str]:
    settings = context.get("settings") if isinstance(context.get("settings"), dict) else {}
    if not bool(settings.get("enabled")) or str(settings.get("mode") or "off").casefold() == "off":
        return {}, "Affiliate policy is not enabled for this Brand."
    query = _post_preview(text)
    if not query:
        return {}, "Post has no text to match against a product."

    addlivetag = context.get("addlivetag") if isinstance(context.get("addlivetag"), dict) else {}
    if addlivetag.get("enabled"):
        try:
            reference = extract_shopee_reference(text)
        except ValueError:
            reference = {}
        if reference:
            try:
                product = normalize_product_payload(fetch_product_data(reference), relevance_score=1.0)
            except (AddLiveTagApiError, TypeError, ValueError) as exc:
                return {}, f"AddLiveTag explicit reference unavailable: {_safe_error(exc)}"
            product["link_provider"] = "addlivetag"
            product["ranking_score"] = 1.0
            raw = product.get("raw") if isinstance(product.get("raw"), dict) else {}
            product["raw"] = {**raw, "_aurex_link_provider": "addlivetag"}
            selected = _policy_product([product], query, settings)
            return (selected, "") if selected else ({}, "Explicit AddLiveTag product does not meet Brand relevance or commission policy.")

    connection = context.get("connection") if isinstance(context.get("connection"), dict) else {}
    if connection.get("connected"):
        try:
            discovered = discover_products(brand, query, limit=10)
        except (RuntimeError, TypeError, ValueError) as exc:
            return {}, f"Official Shopee discovery unavailable: {_safe_error(exc)}"
        products = discovered.get("products") if isinstance(discovered, dict) else []
        selected = _policy_product(products or [], query, settings)
        return (selected, "") if selected else ({}, "Official Shopee has no product meeting Brand relevance or commission policy.")

    try:
        cached = affiliate_store.list_products(query=query, limit=MAX_LIMIT)
    except (RuntimeError, TypeError, ValueError) as exc:
        return {}, f"Cached product lookup unavailable: {_safe_error(exc)}"
    selected = _policy_product(cached, query, settings)
    return (selected, "") if selected else ({}, "No official Shopee connection and no cached product meets Brand policy.")


def _record_for_execute(brand: str, content_id: str, page_id: str, post_id: str, product: dict) -> dict:
    record = _existing_record(brand, content_id, page_id, post_id)
    if record:
        return record
    return affiliate_store.record_content_product({
        "content_id": content_id,
        "brand_id": brand,
        "provider": "shopee",
        "product_id": str(product.get("id") or ""),
        "product_name": str(product.get("name") or "Shopee product"),
        "original_url": str(product.get("origin_url") or ""),
        "commission_rate": _fraction(product.get("commission_rate")),
        "relevance_score": _fraction(product.get("relevance_score")),
        "ranking_score": float(product.get("ranking_score") or 0),
        "placement": "first_comment",
        "page_id": page_id,
        "facebook_post_id": post_id,
        "status": "selected",
    })


def run_affiliate_backfill(
    brand: str,
    *,
    limit: int = 20,
    lookback_days: int = 30,
    dry_run: bool = True,
    page_id: str = "",
) -> dict:
    """Inspect recent Brand Page posts and optionally add one affiliate comment.

    ``dry_run`` is deliberately the default.  Execute mode requires successful
    duplicate inspection before it can create a link or submit a comment.
    """
    result = {
        "ok": False,
        "brand": "",
        "page_id": "",
        "dry_run": bool(dry_run),
        "scanned": 0,
        "eligible": 0,
        "commented": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }
    try:
        brand = canonical_brand(brand)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
            raise ValueError("Brand không hợp lệ.")
        limit = int(limit)
        lookback_days = int(lookback_days)
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit phải từ 1 đến {MAX_LIMIT}.")
        if not 0 <= lookback_days <= MAX_LOOKBACK_DAYS:
            raise ValueError(f"lookback_days phải từ 0 đến {MAX_LOOKBACK_DAYS}.")
        config = read_social_config()
        facebook = facebook_config(config)
        requested_page_id = str(page_id or "").strip()
        page = facebook_upload_page(config, facebook, {"brand": brand, "page_id": requested_page_id})
        resolved_page_id = str(page.get("id") or "").strip()
        if requested_page_id and requested_page_id != resolved_page_id:
            raise ValueError(f"Facebook Page không khớp route của brand {brand}.")
        result.update({"brand": brand, "page_id": resolved_page_id})
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        posts = _read_page_posts(facebook, page, limit=limit, cutoff=cutoff)
        result["scanned"] = len(posts)
        context = brand_context(config, brand)
    except (RuntimeError, TypeError, ValueError) as exc:
        result["failed"] = 1
        result["items"].append({"status": "failed", "reason": _safe_error(exc)})
        return result

    with _BACKFILL_LOCK:
        for post in posts:
            safe_post = _safe_post(post)
            # Top-level fields are the API/UI contract.  The nested copy keeps
            # existing callers convenient without asking the UI to unwrap it.
            item = {**safe_post, "post": dict(safe_post), "status": "skipped", "reason": ""}
            post_id = str(post.get("id") or "")
            content_id = _content_id(brand, resolved_page_id, post_id)
            local = _local_successful_record(brand, content_id, resolved_page_id, post_id)
            if local:
                item["reason"] = "A successful local affiliate comment record already exists."
                result["skipped"] += 1
                result["items"].append(item)
                continue
            try:
                duplicate, duplicate_reason = _read_comments(facebook, page, post_id)
            except (RuntimeError, TypeError, ValueError) as exc:
                item.update({"status": "failed" if not dry_run else "skipped", "reason": f"Comments cannot be inspected: {_safe_error(exc)}"})
                if dry_run:
                    result["skipped"] += 1
                else:
                    result["failed"] += 1
                result["items"].append(item)
                continue
            if duplicate:
                item["reason"] = duplicate_reason or "An affiliate/source comment already exists on Facebook."
                result["skipped"] += 1
                result["items"].append(item)
                continue
            product, reason = _select_product(brand, str(post.get("_text") or ""), context)
            if not product:
                item["reason"] = reason
                result["skipped"] += 1
                result["items"].append(item)
                continue
            result["eligible"] += 1
            item["product"] = _product_summary(product)
            if dry_run:
                item.update({"status": "eligible", "reason": "Dry run: no affiliate link or Facebook comment was created."})
                result["items"].append(item)
                continue
            record: dict = {}
            try:
                # AddLiveTag product data is read-only until this explicit execute path.
                saved_product = affiliate_store.upsert_product(product)
                product = {**product, **saved_product}
                record = _record_for_execute(brand, content_id, resolved_page_id, post_id, product)
                link_result = create_affiliate_link(
                    brand=brand,
                    content_id=content_id,
                    product_id=str(product.get("id") or ""),
                    origin_url=str(product.get("origin_url") or ""),
                    placement="first_comment",
                    page_id=resolved_page_id,
                    product_payload=product,
                    link_provider=str(product.get("link_provider") or ""),
                )
                affiliate_url = str((link_result.get("link") or {}).get("affiliate_url") or "").strip()
                if not affiliate_url:
                    raise RuntimeError("Affiliate link creation returned no URL.")
                comment_id, comment_error = post_facebook_source_comment(
                    facebook,
                    facebook_full_post_id(facebook, post_id, page),
                    affiliate_comment_text(affiliate_url),
                    facebook_page_access_token(facebook, page),
                )
                if not comment_id:
                    affiliate_store.update_content_product(str(record.get("id") or ""), status="failed", error=_safe_error(comment_error))
                    raise RuntimeError(comment_error or "Facebook did not return a comment id.")
                affiliate_store.update_content_product(
                    str(record.get("id") or ""),
                    page_id=resolved_page_id,
                    facebook_post_id=post_id,
                    facebook_comment_id=comment_id,
                    status="commented",
                    error="",
                )
                item.update({"status": "commented", "reason": "Affiliate source comment posted."})
                result["commented"] += 1
            except (RuntimeError, TypeError, ValueError) as exc:
                record_id = str(record.get("id") or "")
                if record_id:
                    try:
                        affiliate_store.update_content_product(record_id, status="failed", error=_safe_error(exc))
                    except (RuntimeError, TypeError, ValueError):
                        pass
                item.update({"status": "failed", "reason": _safe_error(exc)})
                result["failed"] += 1
            result["items"].append(item)
    result["ok"] = True
    return result
