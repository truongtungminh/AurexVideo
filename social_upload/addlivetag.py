from __future__ import annotations

"""Read-only AddLiveTag adapter for explicit Shopee product references.

The product-data endpoint is used only to enrich a Shopee item the caller has
already selected.  The short-link endpoint below is an *experimental web
endpoint*, not the official Shopee Affiliate API.  It is deliberately opt-in:
callers must supply an affiliate id and this module never writes config or
sends cookies, API keys, or other credentials.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .shopee import validate_shopee_url


DEFAULT_PRODUCT_DATA_URL = "https://data.addlivetag.com/product-data/product-data.php"
DEFAULT_SHORT_LINK_URL = "https://addlivetag.com/short-link.php"
PRODUCT_DATA_HOSTS = {"data.addlivetag.com"}
SHORT_LINK_HOSTS = {"addlivetag.com", "www.addlivetag.com"}
MAX_RESPONSE_BYTES = 512 * 1024
MAX_TIMEOUT_SECONDS = 30
ITEM_ID_RE = re.compile(r"[1-9][0-9]{0,31}")
ITEM_ID_LABEL_RE = re.compile(
    r"(?:\bitem[\s_-]*id\b|\bmã\s*sản\s*phẩm\b)\s*(?:[:=#-]\s*)?([1-9][0-9]{0,31})\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
SECRET_KEY_RE = re.compile(r"secret|token|cookie|authorization|api[_-]?key|password", re.IGNORECASE)


class AddLiveTagApiError(RuntimeError):
    """Safe AddLiveTag failure that intentionally omits request URLs and ids."""


def _validated_endpoint(value: object, *, hosts: set[str], label: str) -> str:
    endpoint = str(value or "").strip()
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} phải là HTTPS endpoint thuộc AddLiveTag allowlist.") from exc
    if (
        parsed.scheme != "https"
        or host not in hosts
        or port not in (None, 443)
        or not parsed.path
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} phải là HTTPS endpoint thuộc AddLiveTag allowlist.")
    return urlunparse(("https", parsed.netloc, parsed.path, "", "", ""))


def _timeout(value: object, default: int) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = float(default)
    if timeout <= 0:
        raise ValueError("Timeout phải lớn hơn 0.")
    return min(timeout, float(MAX_TIMEOUT_SECONDS))


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip().replace("\u00a0", " ")
    raw = re.sub(r"[^0-9,.-]", "", raw)
    if not raw:
        return default
    if re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})+", raw):
        raw = raw.replace(".", "")
    elif re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+", raw):
        raw = raw.replace(",", "")
    elif "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return default


def _fraction(value: object) -> float:
    number = _number(value)
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def _price_range(product: Mapping[str, object]) -> tuple[float, float]:
    price = product.get("price")
    if isinstance(price, Mapping):
        minimum = price.get("min", price.get("priceMin", price.get("minPrice", price.get("value", 0))))
        maximum = price.get("max", price.get("priceMax", price.get("maxPrice", minimum)))
    elif isinstance(price, Sequence) and not isinstance(price, (str, bytes, bytearray)):
        minimum = price[0] if price else 0
        maximum = price[-1] if price else minimum
    else:
        minimum = maximum = price
    minimum = _number(product.get("priceMin", product.get("minPrice", minimum)))
    maximum = _number(product.get("priceMax", product.get("maxPrice", maximum)))
    if minimum and not maximum:
        maximum = minimum
    if maximum and not minimum:
        minimum = maximum
    return min(minimum, maximum), max(minimum, maximum)


def _discount_rate(product: Mapping[str, object]) -> float:
    discount = product.get("discountRate")
    if discount in (None, ""):
        discount = product.get("priceDiscountRate")
    if discount in (None, ""):
        history = product.get("latestPriceHistory")
        history = history if isinstance(history, Mapping) else {}
        discount = history.get("discountPercent")
        if discount in (None, ""):
            stats = history.get("priceStats")
            if isinstance(stats, Mapping):
                discount = stats.get("discountPercent", stats.get("discountRate", stats.get("discount", "")))
                if discount in (None, ""):
                    original = _number(stats.get("originalPrice", stats.get("priceBeforeDiscount", 0)))
                    current = _number(stats.get("latestPrice", stats.get("currentPrice", 0)))
                    discount = ((original - current) / original * 100) if original > 0 and current >= 0 else 0
            else:
                discount = stats
    return max(0.0, min(100.0, _number(discount)))


def _optional_shopee_url(*values: object) -> str:
    for value in values:
        candidate = str(value or "").strip()
        if not candidate:
            continue
        try:
            return validate_shopee_url(candidate)
        except ValueError:
            continue
    return ""


def _safe_raw(value: object, *, depth: int = 0) -> object:
    """Keep provider evidence while excluding any accidentally returned secret fields."""
    if depth >= 12:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key): _safe_raw(item, depth=depth + 1)
            for key, item in value.items()
            if not SECRET_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_safe_raw(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _request_json(endpoint: str, params: Mapping[str, str], *, timeout: object, default_timeout: int) -> dict:
    query = urlencode({key: value for key, value in params.items() if value != ""})
    request = Request(
        f"{endpoint}?{query}",
        headers={"Accept": "application/json", "User-Agent": "AurexVideo/addlivetag-readonly"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=_timeout(timeout, default_timeout)) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        try:
            exc.close()
        except OSError:
            pass
        raise AddLiveTagApiError(f"AddLiveTag HTTP {exc.code}.") from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise AddLiveTagApiError("AddLiveTag request failed.") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AddLiveTagApiError("AddLiveTag response vượt quá giới hạn cho phép.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AddLiveTagApiError("AddLiveTag trả về JSON không hợp lệ.") from exc
    if not isinstance(payload, dict):
        raise AddLiveTagApiError("AddLiveTag trả về dữ liệu không hợp lệ.")
    return payload


def extract_shopee_reference(text: object) -> dict[str, str]:
    """Extract one explicit Shopee URL or explicitly-labelled numeric item id.

    A bare number is accepted only when it is the whole input.  Numbers in
    ordinary prose are deliberately ignored to avoid treating prices, dates,
    and view counts as product ids.
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Cần Shopee URL hoặc item ID rõ ràng.")
    candidates = [candidate.rstrip(".,;:!?'\")]}>") for candidate in URL_RE.findall(raw)]
    if candidates:
        if len(candidates) != 1:
            raise ValueError("Chỉ nhận một Shopee URL cho mỗi lần tra cứu.")
        return {"url": validate_shopee_url(candidates[0])}
    if ITEM_ID_RE.fullmatch(raw):
        return {"item_id": raw}
    match = ITEM_ID_LABEL_RE.search(raw)
    if match:
        return {"item_id": match.group(1)}
    raise ValueError("Không nhận diện được Shopee URL hoặc item ID rõ ràng.")


def _reference_params(reference: object) -> dict[str, str]:
    if isinstance(reference, Mapping):
        item_id = str(reference.get("item_id") or reference.get("itemId") or "").strip()
        origin_url = str(reference.get("url") or reference.get("origin_url") or "").strip()
    else:
        extracted = extract_shopee_reference(reference)
        item_id = extracted.get("item_id", "")
        origin_url = extracted.get("url", "")
    if bool(item_id) == bool(origin_url):
        raise ValueError("Cần chính xác một trong item_id hoặc url.")
    if item_id:
        if not ITEM_ID_RE.fullmatch(item_id):
            raise ValueError("item_id phải là số nguyên dương rõ ràng.")
        return {"item_id": item_id}
    return {"url": validate_shopee_url(origin_url)}


def normalize_product_payload(payload: object, *, relevance_score: float = 1.0) -> dict:
    """Normalize a successful product-data response into the affiliate record shape."""
    if not isinstance(payload, Mapping) or str(payload.get("status") or "").casefold() != "success":
        raise AddLiveTagApiError("AddLiveTag không trả về product data thành công.")
    product = payload.get("productInfo")
    if not isinstance(product, Mapping):
        raise AddLiveTagApiError("AddLiveTag productInfo không hợp lệ.")
    item_id = str(product.get("itemId") or product.get("item_id") or "").strip()
    origin_url = str(product.get("originLink") or product.get("productLink") or "").strip()
    if not item_id or not ITEM_ID_RE.fullmatch(item_id):
        raise AddLiveTagApiError("AddLiveTag productInfo thiếu itemId hợp lệ.")
    try:
        origin_url = validate_shopee_url(origin_url)
    except ValueError as exc:
        raise AddLiveTagApiError("AddLiveTag productInfo có Shopee URL không hợp lệ.") from exc
    price_min, price_max = _price_range(product)
    total_rate = product.get("totalRatePercent")
    commission_rate = _fraction(total_rate) if total_rate not in (None, "") else (
        max(0.0, _number(product.get("commission"))) / (price_max or price_min)
        if (price_max or price_min) > 0
        else 0.0
    )
    commission_rate = max(0.0, min(1.0, commission_rate))
    return {
        "provider": "shopee",
        "provider_product_id": item_id,
        "shop_id": str(product.get("shopId") or product.get("shop_id") or "").strip(),
        "name": str(product.get("name") or product.get("productName") or "Shopee product").strip() or "Shopee product",
        "origin_url": origin_url,
        "offer_url": _optional_shopee_url(product.get("affLink"), product.get("affiliateLink")),
        "image_url": str(product.get("imageUrl") or product.get("image_url") or "").strip(),
        "price_min": price_min,
        "price_max": price_max,
        "commission_rate": commission_rate,
        "sales": max(0.0, _number(product.get("sales", product.get("salesCount", product.get("sold", 0))))),
        "rating": max(0.0, min(5.0, _number(product.get("rating", product.get("ratingStar", 0))))),
        "discount_rate": _discount_rate(product),
        "shop_quality": _fraction(product.get("shopQuality", product.get("shop_quality", 0))),
        "relevance_score": max(0.0, min(1.0, _number(relevance_score, 1.0))),
        "raw": _safe_raw({"productInfo": product, "legalNotice": payload.get("legalNotice")}),
    }


def fetch_product_data(
    reference: object,
    *,
    endpoint: str = DEFAULT_PRODUCT_DATA_URL,
    timeout: int = 20,
) -> dict:
    """Fetch one explicit AddLiveTag response payload without normalizing it."""
    endpoint = _validated_endpoint(endpoint, hosts=PRODUCT_DATA_HOSTS, label="Product-data endpoint")
    payload = _request_json(endpoint, _reference_params(reference), timeout=timeout, default_timeout=20)
    if str(payload.get("status") or "").casefold() != "success" or not isinstance(payload.get("productInfo"), Mapping):
        raise AddLiveTagApiError("AddLiveTag không trả về product data thành công.")
    return payload


def fetch_normalized_product_data(
    reference: object,
    *,
    endpoint: str = DEFAULT_PRODUCT_DATA_URL,
    timeout: int = 20,
) -> dict:
    """Compatibility helper for callers that need the affiliate-store record directly."""
    return normalize_product_payload(fetch_product_data(reference, endpoint=endpoint, timeout=timeout), relevance_score=1.0)


def normalize_config(value: object = None, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Normalize caller-selected, Brand-scoped config without reading or writing files.

    Pass the already-selected Brand's raw AddLiveTag section as ``value``.  A
    supplied value wins over the environment; no global config is consulted.
    """
    source = value if isinstance(value, Mapping) else {}
    environment = os.environ if environ is None else environ
    affiliate_id = str(source.get("affiliate_id") or source.get("affiliateId") or source.get("aff_id") or "").strip()
    if not affiliate_id:
        affiliate_id = str(environment.get("ADDLIVETAG_AFFILIATE_ID") or "").strip()
    if affiliate_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", affiliate_id):
        raise ValueError("AddLiveTag affiliate_id không hợp lệ.")
    return {
        "affiliate_id": affiliate_id,
        "product_data_url": _validated_endpoint(
            source.get("product_data_url") or source.get("productDataUrl") or DEFAULT_PRODUCT_DATA_URL,
            hosts=PRODUCT_DATA_HOSTS,
            label="Product-data endpoint",
        ),
        "short_link_url": _validated_endpoint(
            source.get("short_link_url") or source.get("shortLinkUrl") or DEFAULT_SHORT_LINK_URL,
            hosts=SHORT_LINK_HOSTS,
            label="Short-link endpoint",
        ),
    }


def _sub_id_params(sub_ids: object) -> dict[str, str]:
    if sub_ids is None:
        values: list[object] = []
    elif isinstance(sub_ids, Mapping):
        values = [sub_ids.get(f"subid{index}", sub_ids.get(str(index), "")) for index in range(1, 6)]
    elif isinstance(sub_ids, (str, bytes, bytearray)):
        values = [sub_ids]
    else:
        values = list(sub_ids)
    if len(values) > 5:
        raise ValueError("AddLiveTag chỉ nhận tối đa 5 SubID.")
    params = {}
    for index, value in enumerate(values, start=1):
        sub_id = str(value or "").strip()
        if not sub_id:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", sub_id):
            raise ValueError("SubID chỉ được dùng chữ, số, dấu chấm, gạch ngang hoặc gạch dưới.")
        params[f"subid{index}"] = sub_id
    return params


def generate_short_link(
    origin_url: str,
    affiliate_id: str,
    sub_ids: object = None,
    *,
    endpoint: str = DEFAULT_SHORT_LINK_URL,
    timeout: int = 30,
) -> str:
    """Call AddLiveTag's experimental web short-link endpoint for a Shopee URL."""
    endpoint = _validated_endpoint(endpoint, hosts=SHORT_LINK_HOSTS, label="Short-link endpoint")
    origin_url = validate_shopee_url(origin_url)
    affiliate_id = str(affiliate_id or "").strip()
    if not affiliate_id:
        raise ValueError("AddLiveTag cần affiliate_id tường minh để tạo short link.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", affiliate_id):
        raise ValueError("AddLiveTag affiliate_id không hợp lệ.")
    payload = _request_json(
        endpoint,
        {"url": origin_url, "aff_id": affiliate_id, **_sub_id_params(sub_ids)},
        timeout=timeout,
        default_timeout=30,
    )
    if payload.get("success") is not True:
        raise AddLiveTagApiError("AddLiveTag không tạo được short link.")
    link = str(payload.get("affiliateLink") or "").strip()
    try:
        return validate_shopee_url(link)
    except ValueError as exc:
        raise AddLiveTagApiError("AddLiveTag trả về short link Shopee không hợp lệ.") from exc
