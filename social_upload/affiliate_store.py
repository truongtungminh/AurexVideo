from __future__ import annotations

"""Small local persistence layer for the AurexVideo affiliate engine.

The existing social integrations keep credentials in ``social-upload.json``.
This store deliberately keeps only the connection id and reporting data in
SQLite, so the Shopee App Secret follows the same protected config path as the
other social credentials and is not duplicated in analytics rows.
"""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from aurexvideo_paths import CONFIG_ROOT


AFFILIATE_DB_PATH = Path(CONFIG_ROOT).expanduser().resolve() / "affiliate.sqlite3"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS affiliate_accounts (
    id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    api_base_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'configured',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (brand_id, provider)
);

CREATE TABLE IF NOT EXISTS affiliate_brand_settings (
    brand_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'shopee',
    enabled INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'manual',
    placement TEXT NOT NULL DEFAULT 'first_comment',
    products_per_post INTEGER NOT NULL DEFAULT 1,
    min_relevance REAL NOT NULL DEFAULT 0.75,
    min_commission REAL NOT NULL DEFAULT 0.05,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS affiliate_products (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_product_id TEXT NOT NULL DEFAULT '',
    shop_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    origin_url TEXT NOT NULL DEFAULT '',
    offer_url TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    price_min REAL NOT NULL DEFAULT 0,
    price_max REAL NOT NULL DEFAULT 0,
    commission_rate REAL NOT NULL DEFAULT 0,
    sales REAL NOT NULL DEFAULT 0,
    rating REAL NOT NULL DEFAULT 0,
    discount_rate REAL NOT NULL DEFAULT 0,
    shop_quality REAL NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, provider_product_id, origin_url)
);

CREATE TABLE IF NOT EXISTS affiliate_product_pool (
    id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'shopee',
    provider_product_id TEXT NOT NULL DEFAULT '',
    shop_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    origin_url TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    image_url TEXT NOT NULL DEFAULT '',
    price_min REAL NOT NULL DEFAULT 0,
    price_max REAL NOT NULL DEFAULT 0,
    commission_rate REAL NOT NULL DEFAULT 0,
    sales REAL NOT NULL DEFAULT 0,
    rating REAL NOT NULL DEFAULT 0,
    discount_rate REAL NOT NULL DEFAULT 0,
    shop_quality REAL NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (brand_id, provider, origin_url)
);

CREATE TABLE IF NOT EXISTS affiliate_links (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    origin_url TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    sub_id_1 TEXT NOT NULL DEFAULT '',
    sub_id_2 TEXT NOT NULL DEFAULT '',
    sub_id_3 TEXT NOT NULL DEFAULT '',
    sub_id_4 TEXT NOT NULL DEFAULT '',
    sub_id_5 TEXT NOT NULL DEFAULT '',
    placement TEXT NOT NULL DEFAULT 'first_comment',
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    UNIQUE (affiliate_url)
);

CREATE TABLE IF NOT EXISTS content_affiliate_products (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    original_url TEXT NOT NULL DEFAULT '',
    affiliate_url TEXT NOT NULL DEFAULT '',
    commission_rate REAL NOT NULL DEFAULT 0,
    relevance_score REAL NOT NULL DEFAULT 0,
    ranking_score REAL NOT NULL DEFAULT 0,
    placement TEXT NOT NULL DEFAULT 'first_comment',
    page_id TEXT NOT NULL DEFAULT '',
    facebook_post_id TEXT NOT NULL DEFAULT '',
    facebook_comment_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'selected',
    sub_id_1 TEXT NOT NULL DEFAULT '',
    sub_id_2 TEXT NOT NULL DEFAULT '',
    sub_id_3 TEXT NOT NULL DEFAULT '',
    sub_id_4 TEXT NOT NULL DEFAULT '',
    sub_id_5 TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS affiliate_publish_jobs (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    link_id TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'facebook',
    page_id TEXT NOT NULL DEFAULT '',
    post_id TEXT NOT NULL DEFAULT '',
    comment_id TEXT NOT NULL DEFAULT '',
    placement TEXT NOT NULL DEFAULT 'first_comment',
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS affiliate_conversions (
    conversion_id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL DEFAULT '',
    content_id TEXT NOT NULL DEFAULT '',
    product_id TEXT NOT NULL DEFAULT '',
    sub_id_1 TEXT NOT NULL DEFAULT '',
    sub_id_2 TEXT NOT NULL DEFAULT '',
    sub_id_3 TEXT NOT NULL DEFAULT '',
    sub_id_4 TEXT NOT NULL DEFAULT '',
    sub_id_5 TEXT NOT NULL DEFAULT '',
    click_time TEXT NOT NULL DEFAULT '',
    order_time TEXT NOT NULL DEFAULT '',
    order_value REAL NOT NULL DEFAULT 0,
    commission REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS affiliate_daily_stats (
    stat_date TEXT NOT NULL,
    brand_id TEXT NOT NULL DEFAULT '',
    content_id TEXT NOT NULL DEFAULT '',
    product_id TEXT NOT NULL DEFAULT '',
    clicks INTEGER NOT NULL DEFAULT 0,
    orders INTEGER NOT NULL DEFAULT 0,
    gmv REAL NOT NULL DEFAULT 0,
    commission REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (stat_date, brand_id, content_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_affiliate_links_content
    ON affiliate_links (brand_id, content_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_products_updated
    ON content_affiliate_products (brand_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_product_pool_brand
    ON affiliate_product_pool (brand_id, enabled, priority DESC, commission_rate DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_conversions_order
    ON affiliate_conversions (brand_id, order_time);
"""


DEFAULT_SETTINGS = {
    "provider": "shopee",
    "enabled": False,
    "mode": "manual",
    "placement": "first_comment",
    "products_per_post": 1,
    "min_relevance": 0.75,
    "min_commission": 0.05,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _row_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}


def _connect() -> sqlite3.Connection:
    path = Path(AFFILIATE_DB_PATH).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def init_db() -> None:
    with _connect() as connection:
        connection.executescript(SCHEMA)
    try:
        os.chmod(Path(AFFILIATE_DB_PATH), 0o600)
    except OSError:
        pass


def _account_id(brand_id: str, provider: str) -> str:
    digest = hashlib.sha256(f"{provider}:{brand_id}".encode("utf-8")).hexdigest()[:12]
    return f"{provider}-{digest}"


def upsert_account(
    brand_id: str,
    provider: str,
    connection_id: str,
    api_base_url: str,
    *,
    enabled: bool = True,
    status: str = "configured",
    last_error: str = "",
) -> dict:
    init_db()
    now = _now()
    account_id = _account_id(brand_id, provider)
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_accounts
              (id, brand_id, provider, connection_id, api_base_url, enabled, status, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (brand_id, provider) DO UPDATE SET
              connection_id = excluded.connection_id,
              api_base_url = excluded.api_base_url,
              enabled = excluded.enabled,
              status = excluded.status,
              last_error = excluded.last_error,
              updated_at = excluded.updated_at
            """,
            (account_id, brand_id, provider, connection_id, api_base_url, int(bool(enabled)), status, last_error[:1000], now, now),
        )
        row = connection.execute(
            "SELECT * FROM affiliate_accounts WHERE brand_id = ? AND provider = ?",
            (brand_id, provider),
        ).fetchone()
    return _row_dict(row)


def get_account(brand_id: str, provider: str = "shopee") -> dict:
    init_db()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM affiliate_accounts WHERE brand_id = ? AND provider = ?",
            (brand_id, provider),
        ).fetchone()
    return _row_dict(row)


def delete_account(brand_id: str, provider: str = "shopee") -> None:
    init_db()
    with _connect() as connection:
        connection.execute(
            "DELETE FROM affiliate_accounts WHERE brand_id = ? AND provider = ?",
            (brand_id, provider),
        )


def upsert_settings(brand_id: str, values: dict) -> dict:
    init_db()
    current = get_settings(brand_id)
    merged = {**DEFAULT_SETTINGS, **current, **(values if isinstance(values, dict) else {})}
    now = _now()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_brand_settings
              (brand_id, provider, enabled, mode, placement, products_per_post, min_relevance, min_commission, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (brand_id) DO UPDATE SET
              provider = excluded.provider,
              enabled = excluded.enabled,
              mode = excluded.mode,
              placement = excluded.placement,
              products_per_post = excluded.products_per_post,
              min_relevance = excluded.min_relevance,
              min_commission = excluded.min_commission,
              updated_at = excluded.updated_at
            """,
            (
                brand_id,
                str(merged.get("provider") or "shopee"),
                int(bool(merged.get("enabled"))),
                str(merged.get("mode") or "manual"),
                str(merged.get("placement") or "first_comment"),
                int(merged.get("products_per_post") or 1),
                float(merged.get("min_relevance") or 0),
                float(merged.get("min_commission") or 0),
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM affiliate_brand_settings WHERE brand_id = ?",
            (brand_id,),
        ).fetchone()
    result = _row_dict(row)
    result["enabled"] = bool(result.get("enabled"))
    return result


def get_settings(brand_id: str) -> dict:
    init_db()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM affiliate_brand_settings WHERE brand_id = ?",
            (brand_id,),
        ).fetchone()
    if not row:
        return {"brand_id": brand_id, **DEFAULT_SETTINGS}
    result = _row_dict(row)
    result["enabled"] = bool(result.get("enabled"))
    return result


def upsert_product(product: dict) -> dict:
    init_db()
    provider = str(product.get("provider") or "shopee")
    provider_product_id = str(product.get("provider_product_id") or product.get("product_id") or "")
    origin_url = str(product.get("origin_url") or product.get("product_link") or "").strip()
    stable_key = f"{provider}:{provider_product_id}:{origin_url}"
    product_id = str(product.get("id") or "prod_" + hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:16])
    now = _now()
    fields = (
        product_id,
        provider,
        provider_product_id,
        str(product.get("shop_id") or ""),
        str(product.get("name") or product.get("product_name") or "").strip(),
        origin_url,
        str(product.get("offer_url") or product.get("offer_link") or "").strip(),
        str(product.get("image_url") or "").strip(),
        float(product.get("price_min") or 0),
        float(product.get("price_max") or 0),
        float(product.get("commission_rate") or 0),
        float(product.get("sales") or 0),
        float(product.get("rating") or product.get("rating_star") or 0),
        float(product.get("discount_rate") or 0),
        float(product.get("shop_quality") or 0),
        _json(product.get("raw") if "raw" in product else product),
        now,
        now,
    )
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_products
              (id, provider, provider_product_id, shop_id, name, origin_url, offer_url, image_url,
               price_min, price_max, commission_rate, sales, rating, discount_rate, shop_quality,
               raw_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider, provider_product_id, origin_url) DO UPDATE SET
              shop_id = excluded.shop_id, name = excluded.name, offer_url = excluded.offer_url,
              image_url = excluded.image_url, price_min = excluded.price_min, price_max = excluded.price_max,
              commission_rate = excluded.commission_rate, sales = excluded.sales, rating = excluded.rating,
              discount_rate = excluded.discount_rate, shop_quality = excluded.shop_quality,
              raw_json = excluded.raw_json, updated_at = excluded.updated_at
            """,
            fields,
        )
        row = connection.execute(
            "SELECT * FROM affiliate_products WHERE provider = ? AND provider_product_id = ? AND origin_url = ?",
            (provider, provider_product_id, origin_url),
        ).fetchone()
    result = _row_dict(row)
    if result.get("raw_json"):
        try:
            result["raw"] = json.loads(result["raw_json"])
        except (TypeError, json.JSONDecodeError):
            result["raw"] = {}
    result.pop("raw_json", None)
    return result


def get_product(product_id: str) -> dict:
    init_db()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM affiliate_products WHERE id = ?", (str(product_id),)).fetchone()
    result = _row_dict(row)
    if result.get("raw_json"):
        try:
            result["raw"] = json.loads(result["raw_json"])
        except (TypeError, json.JSONDecodeError):
            result["raw"] = {}
    result.pop("raw_json", None)
    return result


def list_products(*, query: str = "", limit: int = 50) -> list[dict]:
    """Return cached provider products without exposing the raw JSON column."""
    init_db()
    limit = max(1, min(int(limit or 50), 100))
    needle = str(query or "").strip()
    filters = ["provider = ?"]
    params: list[object] = ["shopee"]
    if needle:
        filters.append("(name LIKE ? OR origin_url LIKE ? OR offer_url LIKE ?)")
        pattern = f"%{needle}%"
        params.extend([pattern, pattern, pattern])
    params.append(limit)
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM affiliate_products WHERE {' AND '.join(filters)} "
            "ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        raw_json = item.pop("raw_json", "")
        try:
            item["raw"] = json.loads(raw_json) if raw_json else {}
        except (TypeError, json.JSONDecodeError):
            item["raw"] = {}
        result.append(item)
    return result


def _pool_row_result(row: sqlite3.Row | None) -> dict:
    result = _row_dict(row)
    if not result:
        return result
    raw_json = result.pop("raw_json", "")
    try:
        result["raw"] = json.loads(raw_json) if raw_json else {}
    except (TypeError, json.JSONDecodeError):
        result["raw"] = {}
    result["enabled"] = bool(result.get("enabled"))
    result["link_provider"] = "pool"
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    result["raw"] = {**raw, "_aurex_link_provider": "pool"}
    return result


def upsert_product_pool(product: dict) -> dict:
    """Create or update one manually curated, Brand-scoped product.

    Pool rows intentionally keep the affiliate URL separate from the global
    provider catalog.  That lets two Brands use the same Shopee item with
    different hand-picked short links without sharing attribution state.
    """
    init_db()
    product = product if isinstance(product, dict) else {}
    brand_id = str(product.get("brand_id") or product.get("brandId") or "").strip()
    provider = str(product.get("provider") or "shopee").strip().lower()
    origin_url = str(product.get("origin_url") or product.get("originUrl") or "").strip()
    affiliate_url = str(product.get("affiliate_url") or product.get("affiliateUrl") or "").strip()
    if not brand_id or provider != "shopee" or not origin_url or not affiliate_url:
        raise ValueError("Pool Shopee cần Brand, link gốc và link affiliate.")
    name = str(product.get("name") or product.get("product_name") or "").strip()
    if not name:
        raise ValueError("Pool Shopee cần tên sản phẩm.")

    requested_id = str(product.get("id") or product.get("pool_id") or product.get("poolId") or "").strip()
    now = _now()
    raw = product.get("raw") if isinstance(product.get("raw"), dict) else {"source": "manual_pool"}
    fields = {
        "brand_id": brand_id,
        "provider": provider,
        "provider_product_id": str(product.get("provider_product_id") or product.get("product_id") or "").strip(),
        "shop_id": str(product.get("shop_id") or product.get("shopId") or "").strip(),
        "name": name,
        "origin_url": origin_url,
        "affiliate_url": affiliate_url,
        "image_url": str(product.get("image_url") or product.get("imageUrl") or "").strip(),
        "price_min": float(product.get("price_min") or product.get("priceMin") or 0),
        "price_max": float(product.get("price_max") or product.get("priceMax") or 0),
        "commission_rate": float(product.get("commission_rate") or product.get("commissionRate") or 0),
        "sales": float(product.get("sales") or 0),
        "rating": float(product.get("rating") or 0),
        "discount_rate": float(product.get("discount_rate") or product.get("discountRate") or 0),
        "shop_quality": float(product.get("shop_quality") or product.get("shopQuality") or 0),
        "priority": int(product.get("priority") or 0),
        "enabled": int(bool(product.get("enabled", True))),
        "note": str(product.get("note") or "").strip()[:1000],
        "raw_json": _json(raw),
        "updated_at": now,
    }
    with _connect() as connection:
        existing_by_id = None
        if requested_id:
            existing_by_id = connection.execute(
                "SELECT * FROM affiliate_product_pool WHERE id = ?",
                (requested_id,),
            ).fetchone()
            if existing_by_id and str(existing_by_id["brand_id"]) != brand_id:
                raise ValueError("Không thể sửa sản phẩm pool của Brand khác.")
        existing_by_key = connection.execute(
            "SELECT * FROM affiliate_product_pool WHERE brand_id = ? AND provider = ? AND origin_url = ?",
            (brand_id, provider, origin_url),
        ).fetchone()
        if existing_by_key and existing_by_id and str(existing_by_key["id"]) != str(existing_by_id["id"]):
            raise ValueError("Sản phẩm với link gốc này đã có trong pool của Brand.")

        pool_id = str((existing_by_id or existing_by_key)["id"]) if (existing_by_id or existing_by_key) else (requested_id or _new_id("pool_product"))
        created_at = str((existing_by_id or existing_by_key)["created_at"]) if (existing_by_id or existing_by_key) else now
        values = (pool_id, *fields.values(), created_at)
        if existing_by_id or existing_by_key:
            connection.execute(
                """
                UPDATE affiliate_product_pool SET
                    brand_id = ?, provider = ?, provider_product_id = ?, shop_id = ?, name = ?,
                    origin_url = ?, affiliate_url = ?, image_url = ?, price_min = ?, price_max = ?,
                    commission_rate = ?, sales = ?, rating = ?, discount_rate = ?, shop_quality = ?,
                    priority = ?, enabled = ?, note = ?, raw_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (*fields.values(), pool_id),
            )
        else:
            connection.execute(
                """
                INSERT INTO affiliate_product_pool
                  (id, brand_id, provider, provider_product_id, shop_id, name, origin_url, affiliate_url,
                   image_url, price_min, price_max, commission_rate, sales, rating, discount_rate,
                   shop_quality, priority, enabled, note, raw_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        row = connection.execute("SELECT * FROM affiliate_product_pool WHERE id = ?", (pool_id,)).fetchone()
    return _pool_row_result(row)


def get_product_pool(pool_id: str, *, brand_id: str = "") -> dict:
    init_db()
    filters = ["id = ?"]
    params: list[object] = [str(pool_id or "")]
    if brand_id:
        filters.append("brand_id = ?")
        params.append(str(brand_id))
    with _connect() as connection:
        row = connection.execute(
            f"SELECT * FROM affiliate_product_pool WHERE {' AND '.join(filters)}",
            tuple(params),
        ).fetchone()
    return _pool_row_result(row)


def list_product_pool(
    brand_id: str,
    *,
    query: str = "",
    enabled_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    """Return only the manually curated pool rows for one Brand."""
    init_db()
    limit = max(1, min(int(limit or 100), 200))
    filters = ["brand_id = ?", "provider = ?"]
    params: list[object] = [str(brand_id or ""), "shopee"]
    if enabled_only:
        filters.append("enabled = 1")
    needle = str(query or "").strip()
    if needle:
        filters.append("(name LIKE ? OR origin_url LIKE ? OR affiliate_url LIKE ? OR note LIKE ?)")
        pattern = f"%{needle}%"
        params.extend([pattern, pattern, pattern, pattern])
    params.append(limit)
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM affiliate_product_pool WHERE {' AND '.join(filters)} "
            "ORDER BY enabled DESC, priority DESC, commission_rate DESC, updated_at DESC, name COLLATE NOCASE ASC LIMIT ?",
            tuple(params),
        ).fetchall()
    return [_pool_row_result(row) for row in rows]


def delete_product_pool(pool_id: str, *, brand_id: str) -> dict:
    """Delete one pool row only when it belongs to the requested Brand."""
    init_db()
    pool_id = str(pool_id or "").strip()
    brand_id = str(brand_id or "").strip()
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM affiliate_product_pool WHERE id = ? AND brand_id = ?",
            (pool_id, brand_id),
        ).fetchone()
        if not row:
            return {"ok": False, "id": pool_id, "deleted": False}
        connection.execute(
            "DELETE FROM affiliate_product_pool WHERE id = ? AND brand_id = ?",
            (pool_id, brand_id),
        )
    return {"ok": True, "id": pool_id, "deleted": True}


def product_conversion_rates(provider: str = "shopee") -> dict[str, float]:
    """Return observed order/click rates keyed by provider id and origin URL."""
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.provider_product_id, p.origin_url,
                   COALESCE(SUM(s.orders), 0) AS orders,
                   COALESCE(SUM(s.clicks), 0) AS clicks
            FROM affiliate_products p
            LEFT JOIN affiliate_daily_stats s
              ON s.product_id = p.id OR s.product_id = p.provider_product_id
            WHERE p.provider = ?
            GROUP BY p.id, p.provider_product_id, p.origin_url
            """,
            (str(provider or "shopee"),),
        ).fetchall()
    rates: dict[str, float] = {}
    for row in rows:
        clicks = float(row["clicks"] or 0)
        if clicks <= 0:
            continue
        rate = max(0.0, min(1.0, float(row["orders"] or 0) / clicks))
        for key in (row["id"], row["provider_product_id"], row["origin_url"]):
            key = str(key or "").strip()
            if key:
                rates[key] = rate
    return rates


def record_link(link: dict) -> dict:
    init_db()
    link_id = str(link.get("id") or _new_id("link"))
    created_at = str(link.get("created_at") or _now())
    values = (
        link_id,
        str(link.get("content_id") or ""),
        str(link.get("brand_id") or ""),
        str(link.get("provider") or "shopee"),
        str(link.get("product_id") or ""),
        str(link.get("origin_url") or ""),
        str(link.get("affiliate_url") or ""),
        *[str(link.get(f"sub_id_{index}") or "") for index in range(1, 6)],
        str(link.get("placement") or "first_comment"),
        str(link.get("status") or "created"),
        created_at,
    )
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_links
              (id, content_id, brand_id, provider, product_id, origin_url, affiliate_url,
               sub_id_1, sub_id_2, sub_id_3, sub_id_4, sub_id_5, placement, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (affiliate_url) DO UPDATE SET
              content_id = excluded.content_id, brand_id = excluded.brand_id,
              product_id = excluded.product_id, placement = excluded.placement,
              status = excluded.status
            """,
            values,
        )
        row = connection.execute("SELECT * FROM affiliate_links WHERE affiliate_url = ?", (values[6],)).fetchone()
    return _row_dict(row)


def record_content_product(record: dict) -> dict:
    init_db()
    record_id = str(record.get("id") or _new_id("content_affiliate"))
    now = _now()
    columns = (
        "id", "content_id", "brand_id", "provider", "product_id", "product_name", "original_url",
        "affiliate_url", "commission_rate", "relevance_score", "ranking_score", "placement", "page_id",
        "facebook_post_id", "facebook_comment_id", "status", "sub_id_1", "sub_id_2", "sub_id_3",
        "sub_id_4", "sub_id_5", "error", "created_at", "updated_at",
    )
    values = (
        record_id,
        str(record.get("content_id") or ""),
        str(record.get("brand_id") or ""),
        str(record.get("provider") or "shopee"),
        str(record.get("product_id") or ""),
        str(record.get("product_name") or ""),
        str(record.get("original_url") or ""),
        str(record.get("affiliate_url") or ""),
        float(record.get("commission_rate") or 0),
        float(record.get("relevance_score") or 0),
        float(record.get("ranking_score") or 0),
        str(record.get("placement") or "first_comment"),
        str(record.get("page_id") or ""),
        str(record.get("facebook_post_id") or ""),
        str(record.get("facebook_comment_id") or ""),
        str(record.get("status") or "selected"),
        *[str(record.get(f"sub_id_{index}") or "") for index in range(1, 6)],
        str(record.get("error") or "")[:1000],
        str(record.get("created_at") or now),
        now,
    )
    with _connect() as connection:
        connection.execute(
            f"INSERT INTO content_affiliate_products ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        row = connection.execute("SELECT * FROM content_affiliate_products WHERE id = ?", (record_id,)).fetchone()
    return _row_dict(row)


def update_content_product(record_id: str, **values: object) -> dict:
    init_db()
    allowed = {
        "page_id", "facebook_post_id", "facebook_comment_id", "status", "error", "affiliate_url",
    }
    updates = {key: values[key] for key in allowed if key in values}
    if not updates:
        return {}
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _connect() as connection:
        connection.execute(
            f"UPDATE content_affiliate_products SET {assignments} WHERE id = ?",
            (*updates.values(), str(record_id)),
        )
        row = connection.execute("SELECT * FROM content_affiliate_products WHERE id = ?", (str(record_id),)).fetchone()
    return _row_dict(row)


def record_publish_job(job: dict) -> dict:
    init_db()
    job_id = str(job.get("id") or _new_id("affiliate_job"))
    now = _now()
    values = (
        job_id,
        str(job.get("content_id") or ""),
        str(job.get("brand_id") or ""),
        str(job.get("provider") or "shopee"),
        str(job.get("link_id") or ""),
        str(job.get("platform") or "facebook"),
        str(job.get("page_id") or ""),
        str(job.get("post_id") or ""),
        str(job.get("comment_id") or ""),
        str(job.get("placement") or "first_comment"),
        str(job.get("status") or "queued"),
        str(job.get("error") or "")[:1000],
        str(job.get("created_at") or now),
        now,
    )
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_publish_jobs
              (id, content_id, brand_id, provider, link_id, platform, page_id, post_id, comment_id,
               placement, status, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        row = connection.execute("SELECT * FROM affiliate_publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_dict(row)


def update_publish_job(job_id: str, **values: object) -> dict:
    init_db()
    allowed = {"page_id", "post_id", "comment_id", "status", "error", "link_id"}
    updates = {key: values[key] for key in allowed if key in values}
    if not updates:
        return {}
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _connect() as connection:
        connection.execute(f"UPDATE affiliate_publish_jobs SET {assignments} WHERE id = ?", (*updates.values(), str(job_id)))
        row = connection.execute("SELECT * FROM affiliate_publish_jobs WHERE id = ?", (str(job_id),)).fetchone()
    return _row_dict(row)


def upsert_daily_stats(
    stat_date: str,
    *,
    brand_id: str = "",
    content_id: str = "",
    product_id: str = "",
    clicks: int = 0,
    orders: int = 0,
    gmv: float = 0,
    commission: float = 0,
) -> dict:
    init_db()
    values = (stat_date, brand_id, content_id, product_id, int(clicks), int(orders), float(gmv), float(commission))
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_daily_stats
              (stat_date, brand_id, content_id, product_id, clicks, orders, gmv, commission)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (stat_date, brand_id, content_id, product_id) DO UPDATE SET
              clicks = excluded.clicks, orders = excluded.orders,
              gmv = excluded.gmv, commission = excluded.commission
            """,
            values,
        )
        row = connection.execute(
            "SELECT * FROM affiliate_daily_stats WHERE stat_date = ? AND brand_id = ? AND content_id = ? AND product_id = ?",
            values[:4],
        ).fetchone()
    return _row_dict(row)


def record_conversion(conversion: dict) -> dict:
    init_db()
    conversion_id = str(conversion.get("conversion_id") or conversion.get("id") or _new_id("conversion"))
    created_at = _now()
    values = (
        conversion_id,
        str(conversion.get("brand_id") or ""),
        str(conversion.get("content_id") or ""),
        str(conversion.get("product_id") or ""),
        *[str(conversion.get(f"sub_id_{index}") or "") for index in range(1, 6)],
        str(conversion.get("click_time") or ""),
        str(conversion.get("order_time") or ""),
        float(conversion.get("order_value") or 0),
        float(conversion.get("commission") or 0),
        str(conversion.get("status") or ""),
        _json(conversion.get("raw") if "raw" in conversion else conversion),
        created_at,
    )
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_conversions
              (conversion_id, brand_id, content_id, product_id, sub_id_1, sub_id_2, sub_id_3, sub_id_4, sub_id_5,
               click_time, order_time, order_value, commission, status, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (conversion_id) DO UPDATE SET
              brand_id = excluded.brand_id, content_id = excluded.content_id, product_id = excluded.product_id,
              click_time = excluded.click_time, order_time = excluded.order_time, order_value = excluded.order_value,
              commission = excluded.commission, status = excluded.status, raw_json = excluded.raw_json
            """,
            values,
        )
        row = connection.execute("SELECT * FROM affiliate_conversions WHERE conversion_id = ?", (conversion_id,)).fetchone()
    return _row_dict(row)


def rebuild_daily_stats_from_conversions(keys: Iterable[tuple[str, str, str, str]]) -> int:
    """Rebuild affected day/Brand/content/product stats idempotently."""
    unique_keys = list(dict.fromkeys(
        (str(stat_date or ""), str(brand_id or ""), str(content_id or ""), str(product_id or ""))
        for stat_date, brand_id, content_id, product_id in keys
    ))
    if not unique_keys:
        return 0
    aggregates: list[tuple[str, str, str, str, int, int, float, float]] = []
    with _connect() as connection:
        for stat_date, brand_id, content_id, product_id in unique_keys:
            rows = connection.execute(
                """
                SELECT raw_json, order_value, commission
                FROM affiliate_conversions
                WHERE brand_id = ? AND content_id = ? AND product_id = ?
                  AND substr(COALESCE(NULLIF(order_time, ''), NULLIF(click_time, ''), created_at), 1, 10) = ?
                """,
                (brand_id, content_id, product_id, stat_date),
            ).fetchall()
            clicks = 0
            orders = 0
            gmv = 0.0
            commission = 0.0
            for row in rows:
                try:
                    raw = json.loads(row["raw_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    raw = {}
                try:
                    clicks += max(0, int(float(raw.get("clicks") or 0)))
                except (TypeError, ValueError):
                    pass
                try:
                    orders += max(0, int(float(raw.get("orders") or 1)))
                except (TypeError, ValueError):
                    orders += 1
                try:
                    gmv += float(row["order_value"] or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    commission += float(row["commission"] or 0)
                except (TypeError, ValueError):
                    pass
            aggregates.append((stat_date, brand_id, content_id, product_id, clicks, orders, gmv, commission))
    for stat_date, brand_id, content_id, product_id, clicks, orders, gmv, commission in aggregates:
        upsert_daily_stats(
            stat_date,
            brand_id=brand_id,
            content_id=content_id,
            product_id=product_id,
            clicks=clicks,
            orders=orders,
            gmv=gmv,
            commission=commission,
        )
    return len(aggregates)


def overview(
    *,
    brand_id: str = "",
    content_id: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 20,
) -> dict:
    init_db()
    limit = max(1, min(int(limit or 20), 100))
    stats_filters = ["1 = 1"]
    stats_params: list[object] = []
    if brand_id:
        stats_filters.append("brand_id = ?")
        stats_params.append(brand_id)
    if content_id:
        stats_filters.append("content_id = ?")
        stats_params.append(content_id)
    if start_date:
        stats_filters.append("stat_date >= ?")
        stats_params.append(start_date)
    if end_date:
        stats_filters.append("stat_date <= ?")
        stats_params.append(end_date)
    where = " AND ".join(stats_filters)
    link_filters = ["1 = 1"]
    link_params: list[object] = []
    if brand_id:
        link_filters.append("l.brand_id = ?")
        link_params.append(brand_id)
    if content_id:
        link_filters.append("l.content_id = ?")
        link_params.append(content_id)
    with _connect() as connection:
        kpi = connection.execute(
            f"SELECT COALESCE(SUM(clicks), 0) AS clicks, COALESCE(SUM(orders), 0) AS orders, "
            f"COALESCE(SUM(gmv), 0) AS gmv, COALESCE(SUM(commission), 0) AS commission "
            f"FROM affiliate_daily_stats WHERE {where}",
            stats_params,
        ).fetchone()
        links = connection.execute(
            f"""
            SELECT l.*, p.name AS product_name, p.image_url, p.price_min, p.price_max,
                   p.commission_rate AS product_commission_rate
            FROM affiliate_links l
            LEFT JOIN affiliate_products p ON p.id = l.product_id
            WHERE {' AND '.join(link_filters)}
            ORDER BY l.created_at DESC
            LIMIT ?
            """,
            (*link_params, limit),
        ).fetchall()
        products = connection.execute(
            f"""
            SELECT c.*, p.image_url, p.price_min, p.price_max
            FROM content_affiliate_products c
            LEFT JOIN affiliate_products p ON p.id = c.product_id
            WHERE {' AND '.join(['1 = 1'] + (["c.brand_id = ?"] if brand_id else []) + (["c.content_id = ?"] if content_id else []))}
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (*( [brand_id] if brand_id else []), *( [content_id] if content_id else []), limit),
        ).fetchall()
        campaigns = connection.execute(
            f"""
            SELECT content_id, brand_id, COUNT(*) AS links,
                   MAX(created_at) AS updated_at,
                   MAX(product_name) AS product_name,
                   MAX(ranking_score) AS ranking_score,
                   MAX(affiliate_url) AS affiliate_url
            FROM content_affiliate_products
            WHERE {' AND '.join(['1 = 1'] + (["brand_id = ?"] if brand_id else []) + (["content_id = ?"] if content_id else []))}
            GROUP BY brand_id, content_id
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*( [brand_id] if brand_id else []), *( [content_id] if content_id else []), limit),
        ).fetchall()
    kpi_result = _row_dict(kpi)
    kpi_result = {key: (int(value or 0) if key in {"clicks", "orders"} else float(value or 0)) for key, value in kpi_result.items()}
    clicks = kpi_result["clicks"]
    kpi_result["ctr"] = 0.0
    kpi_result["conversion_rate"] = (kpi_result["orders"] / clicks) if clicks else 0.0
    return {
        "kpis": kpi_result,
        "links": [_row_dict(row) for row in links],
        "products": [_row_dict(row) for row in products],
        "campaigns": [_row_dict(row) for row in campaigns],
        "filters": {"brand_id": brand_id, "content_id": content_id, "start_date": start_date, "end_date": end_date},
    }


def list_recent_conversions(*, brand_id: str = "", limit: int = 20) -> list[dict]:
    init_db()
    limit = max(1, min(int(limit or 20), 100))
    clause = "WHERE brand_id = ?" if brand_id else ""
    params: Iterable[object] = (brand_id, limit) if brand_id else (limit,)
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM affiliate_conversions {clause} ORDER BY COALESCE(order_time, click_time, created_at) DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    return [_row_dict(row) for row in rows]
