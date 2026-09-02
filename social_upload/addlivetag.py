from __future__ import annotations

"""Read-only AddLiveTag adapter for explicit Shopee product references.

The product-data endpoint is used only to enrich a Shopee item the caller has
already selected.  The default short-link route is AddLiveTag's
Affiliate-ID-aware ``/short-link.php`` helper, so the configured Affiliate ID
is preserved in the generated link.  AddLiveTag's
``shopee-affiliate-api/api_handler.php`` trial wrapper is retained as an
explicit opt-in route; its server-side API account must match the Brand before
it is safe to use for attribution.  This module is deliberately opt-in:
callers must supply an affiliate id and it never writes config or sends
cookies, API keys, or other credentials.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .shopee import validate_shopee_url


DEFAULT_PRODUCT_DATA_URL = "https://data.addlivetag.com/product-data/product-data.php"
DEFAULT_SHORT_LINK_URL = "https://addlivetag.com/short-link.php"
DEFAULT_SHORT_LINK_API_URL = "https://addlivetag.com/shopee-affiliate-api/api_handler.php"
DEFAULT_SEARCH_URL = "https://addlivetag.com/live/search.php"
SHORT_LINK_API_PATH = "/shopee-affiliate-api/api_handler.php"
SHORT_LINK_DOC_PATH = "/shopee-affiliate-api/short_link.php"
PRODUCT_DATA_HOSTS = {"data.addlivetag.com"}
SHORT_LINK_HOSTS = {"addlivetag.com", "www.addlivetag.com"}
SEARCH_HOSTS = SHORT_LINK_HOSTS
AFFILIATE_ID_QUERY_KEYS = ("affiliate_id", "affiliateId", "aff_id")
AFFILIATE_SOURCE_QUERY_KEYS = ("mmp_pid", "utm_source")
MAX_RESPONSE_BYTES = 512 * 1024
MAX_TIMEOUT_SECONDS = 30
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_QUERY_LENGTH = 180
ITEM_ID_RE = re.compile(r"[1-9][0-9]{0,31}")
ITEM_ID_LABEL_RE = re.compile(
    r"(?:\bitem[\s_-]*id\b|\bmã\s*sản\s*phẩm\b)\s*(?:[:=#-]\s*)?([1-9][0-9]{0,31})\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
SECRET_KEY_RE = re.compile(r"secret|token|cookie|authorization|api[_-]?key|password", re.IGNORECASE)


class AddLiveTagApiError(RuntimeError):
    """Safe AddLiveTag failure that intentionally omits request URLs and ids."""


def _clean_html_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class _LiveSearchTableParser(HTMLParser):
    """Extract product rows without depending on a third-party HTML package."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, object]]] = []
        self._row: list[dict[str, object]] | None = None
        self._cell: dict[str, object] | None = None
        self._link: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "tr" and self._row is None:
            self._row = []
            return
        if tag == "td" and self._row is not None and self._cell is None:
            self._cell = {"parts": [], "links": []}
            return
        if tag == "a" and self._cell is not None and self._link is None:
            values = dict(attrs)
            self._link = {"href": str(values.get("href") or ""), "parts": []}

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Images and other self-closing elements do not contribute to the
        # searchable product fields; keeping this explicit avoids opening a
        # phantom cell for malformed provider markup.
        return

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        parts = self._cell["parts"]
        if isinstance(parts, list):
            parts.append(data)
        if self._link is not None:
            link_parts = self._link["parts"]
            if isinstance(link_parts, list):
                link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._cell is not None and self._link is not None:
            links = self._cell["links"]
            if isinstance(links, list):
                links.append({
                    "href": str(self._link.get("href") or ""),
                    "text": _clean_html_text(" ".join(str(part) for part in self._link.get("parts", []))),
                })
            self._link = None
            return
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append({
                "text": _clean_html_text(" ".join(str(part) for part in self._cell.get("parts", []))),
                "links": list(self._cell.get("links") or []),
            })
            self._cell = None
            return
        if tag == "tr" and self._row is not None:
            if self._cell is not None:
                self._row.append({
                    "text": _clean_html_text(" ".join(str(part) for part in self._cell.get("parts", []))),
                    "links": list(self._cell.get("links") or []),
                })
                self._cell = None
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _request_text(endpoint: str, params: Mapping[str, str], *, timeout: object, default_timeout: int) -> str:
    query = urlencode({key: value for key, value in params.items() if value != ""})
    request = Request(
        f"{endpoint}?{query}",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "AurexVideo/addlivetag-readonly",
        },
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
    return raw.decode("utf-8", "replace")


def _search_number(value: object) -> float | None:
    text = _clean_html_text(value)
    if not text or not re.fullmatch(r"[0-9][0-9.,\s₫đĐvVnNdD]*", text):
        return None
    number = _number(text, default=-1)
    return number if number >= 0 else None


def _search_product_from_row(row: list[dict[str, object]], query: str) -> dict | None:
    cells = [cell if isinstance(cell, dict) else {} for cell in row]
    origin_url = ""
    name = ""
    url_cell_index = -1
    for index, cell in enumerate(cells):
        cell_text = str(cell.get("text") or "")
        candidates = [cell_text]
        links = cell.get("links")
        if isinstance(links, list):
            candidates.extend(str(link.get("href") or "") for link in links if isinstance(link, dict))
            if not name:
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    link_text = _clean_html_text(link.get("text"))
                    if link_text and not re.fullmatch(r"https?://[^\s]+", link_text, flags=re.IGNORECASE):
                        name = link_text
                        break
        for candidate in candidates:
            try:
                origin_url = validate_shopee_url(candidate)
            except ValueError:
                continue
            url_cell_index = index
            break
        if origin_url:
            break
    if not origin_url or url_cell_index < 0:
        return None
    if not name:
        name = str(cells[2].get("text") or "") if len(cells) > 2 else ""
    name = re.sub(r"^xtra\s+", "", _clean_html_text(name), flags=re.IGNORECASE)
    if not name:
        return None

    parsed = urlparse(origin_url)
    match = re.search(r"/bc-i\.([1-9][0-9]{0,31})\.([1-9][0-9]{0,31})(?:[/?#]|$)", parsed.path)
    if not match:
        return None
    shop_id, item_id = match.groups()
    values = []
    for cell in cells[url_cell_index + 1:]:
        number = _search_number(cell.get("text"))
        if number is not None:
            values.append(number)
    if len(values) < 2:
        # Anonymous search results can expose the URL but hide commercial
        # fields behind login.  They are not safe candidates for AUTO.
        return None
    commission_amount, price = values[:2]
    sold = values[2] if len(values) > 2 else 0.0
    commission_rate = commission_amount / price if price > 0 else 0.0
    commission_rate = max(0.0, min(1.0, commission_rate))
    return {
        "provider": "shopee",
        "provider_product_id": item_id,
        "shop_id": shop_id,
        "name": name,
        "origin_url": origin_url,
        "offer_url": "",
        "image_url": "",
        "price_min": price,
        "price_max": price,
        "commission_rate": commission_rate,
        "commission_amount_vnd": commission_amount,
        "sales": max(0.0, sold),
        "rating": 0.0,
        "discount_rate": 0.0,
        "shop_quality": 0.0,
        "link_provider": "addlivetag",
        "raw": {
            "source": "addlivetag_live_search",
            "query": query,
            "commission_amount_vnd": commission_amount,
            "price_vnd": price,
            "sold": sold,
        },
    }


def search_addlivetag_products(
    query: object,
    *,
    endpoint: str = DEFAULT_SEARCH_URL,
    limit: int = 20,
    timeout: int = 20,
) -> list[dict]:
    """Read keyword-matched Shopee candidates from AddLiveTag's public catalog.

    This is intentionally an HTML adapter because AddLiveTag's public LIVE
    search is not the official Shopee Open API.  It never sends credentials,
    cookies, or an Affiliate ID, and it rejects rows without visible price and
    commission data so the caller can enforce Brand policy conservatively.
    """
    keyword = " ".join(str(query or "").split())[:MAX_SEARCH_QUERY_LENGTH]
    if len(keyword) < 2:
        return []
    try:
        result_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("AddLiveTag search limit không hợp lệ.") from exc
    if not 1 <= result_limit <= MAX_SEARCH_RESULTS:
        raise ValueError(f"AddLiveTag search limit phải từ 1 đến {MAX_SEARCH_RESULTS}.")
    endpoint = _validated_endpoint(endpoint, hosts=SEARCH_HOSTS, label="Search endpoint")
    html = _request_text(
        endpoint,
        {"keyword": keyword, "limit": str(result_limit), "sold": "0", "price": "0", "sort": "com"},
        timeout=timeout,
        default_timeout=20,
    )
    parser = _LiveSearchTableParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise AddLiveTagApiError("AddLiveTag search trả về HTML không hợp lệ.") from exc
    products: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in parser.rows:
        product = _search_product_from_row(row, keyword)
        if not product:
            continue
        key = (str(product.get("provider_product_id") or ""), str(product.get("origin_url") or ""))
        if key in seen:
            continue
        seen.add(key)
        products.append(product)
        if len(products) >= result_limit:
            break
    return products


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


def _request_json_post(
    endpoint: str,
    payload: Mapping[str, object],
    *,
    timeout: object,
    default_timeout: int,
) -> dict:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        endpoint,
        data=encoded,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AurexVideo/addlivetag-readonly",
        },
        method="POST",
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
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AddLiveTagApiError("AddLiveTag trả về JSON không hợp lệ.") from exc
    if not isinstance(parsed, dict):
        raise AddLiveTagApiError("AddLiveTag trả về dữ liệu không hợp lệ.")
    return parsed


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
    short_link_endpoint = _canonical_short_link_endpoint(_validated_endpoint(
        source.get("short_link_url") or source.get("shortLinkUrl") or DEFAULT_SHORT_LINK_URL,
        hosts=SHORT_LINK_HOSTS,
        label="Short-link endpoint",
    ))
    return {
        "affiliate_id": affiliate_id,
        "product_data_url": _validated_endpoint(
            source.get("product_data_url") or source.get("productDataUrl") or DEFAULT_PRODUCT_DATA_URL,
            hosts=PRODUCT_DATA_HOSTS,
            label="Product-data endpoint",
        ),
        "short_link_url": short_link_endpoint,
    }


def _sub_id_values(sub_ids: object) -> list[object]:
    if sub_ids is None:
        return []
    elif isinstance(sub_ids, Mapping):
        values = []
        for index in range(1, 6):
            if f"subid{index}" in sub_ids:
                values.append(sub_ids.get(f"subid{index}"))
            elif f"sub{index}" in sub_ids:
                values.append(sub_ids.get(f"sub{index}"))
            else:
                values.append(sub_ids.get(str(index), ""))
    elif isinstance(sub_ids, (str, bytes, bytearray)):
        values = [sub_ids]
    else:
        values = list(sub_ids)
    if len(values) > 5:
        raise ValueError("AddLiveTag chỉ nhận tối đa 5 SubID.")
    return values


def _sub_id_params(sub_ids: object) -> dict[str, str]:
    values = _sub_id_values(sub_ids)
    params = {}
    for index, value in enumerate(values, start=1):
        sub_id = str(value or "").strip()
        if not sub_id:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", sub_id):
            raise ValueError("SubID chỉ được dùng chữ, số, dấu chấm, gạch ngang hoặc gạch dưới.")
        params[f"subid{index}"] = sub_id
    return params


def _addlivetag_sub_id_params(sub_ids: object) -> dict[str, str]:
    """Map Aurex tracking values to the handler's ``sub1`` ... ``sub5`` fields.

    The AddLiveTag wrapper currently rejects punctuation in some Sub IDs even
    though the underlying documentation describes them as strings.  Keep the
    original values in Aurex's local link record, but send a compact ASCII
    form to the provider so a normal Brand/page/content slug does not make the
    short-link request fail.
    """
    params = {}
    for index, value in enumerate(_sub_id_values(sub_ids), start=1):
        raw = str(value or "").strip()
        if not raw:
            continue
        compact = re.sub(r"[^A-Za-z0-9]", "", raw)[:64]
        if compact:
            params[f"sub{index}"] = compact
    return params


def _canonical_short_link_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.path.rstrip("/").casefold() != SHORT_LINK_DOC_PATH:
        return endpoint
    return urlunparse(("https", parsed.netloc, SHORT_LINK_API_PATH, "", "", ""))


def _is_short_link_api_endpoint(endpoint: str) -> bool:
    return urlparse(endpoint).path.rstrip("/").casefold() == SHORT_LINK_API_PATH


def _provider_response_text(value: object, *, depth: int = 0) -> str:
    """Flatten provider error metadata for classification without returning it."""
    if depth >= 10:
        return ""
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            parts.append(str(key))
            parts.append(_provider_response_text(item, depth=depth + 1))
        return " ".join(parts)
    if isinstance(value, list):
        return " ".join(_provider_response_text(item, depth=depth + 1) for item in value[:50])
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _short_link_error_message(payload: Mapping[str, object]) -> str:
    provider_text = _provider_response_text(payload).casefold()
    if "rate limit" in provider_text or "rate_limit" in provider_text:
        return "AddLiveTag Short Link API đang vượt giới hạn thử nghiệm."
    if "invalid sub id" in provider_text or "invalid subid" in provider_text:
        return "AddLiveTag Short Link API từ chối Sub ID."
    return "AddLiveTag không tạo được short link."


def _payload_has_invalid_sub_id(payload: Mapping[str, object]) -> bool:
    provider_text = _provider_response_text(payload).casefold()
    return "invalid sub id" in provider_text or "invalid subid" in provider_text


def _extract_short_link(payload: Mapping[str, object]) -> str:
    if payload.get("success") is not True:
        return ""
    candidates = [payload.get("affiliateLink"), payload.get("shortLink")]
    data = payload.get("data")
    if isinstance(data, Mapping):
        candidates.append(data.get("shortLink"))
        generated = data.get("generateShortLink")
        if isinstance(generated, Mapping):
            candidates.append(generated.get("shortLink"))
        nested = data.get("data")
        if isinstance(nested, Mapping):
            generated = nested.get("generateShortLink")
            if isinstance(generated, Mapping):
                candidates.append(generated.get("shortLink"))
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "")


def validate_addlivetag_attribution(
    link: object,
    affiliate_id: object,
    *,
    require_attribution: bool = False,
) -> str:
    """Validate a Shopee-hosted link against the selected Brand's Affiliate ID.

    AddLiveTag's legacy endpoint echoes the attribution in the returned
    ``s.shopee.vn/an_redir`` URL.  Checking that echo prevents a cached or
    misconfigured provider link from silently crediting another account.  The
    API-handler route can return a clean slug without query metadata, so its
    attribution is only required when the caller explicitly opts into the
    strict check.
    """
    try:
        normalized = validate_shopee_url(str(link or "").strip())
    except ValueError as exc:
        raise AddLiveTagApiError("AddLiveTag trả về short link Shopee không hợp lệ.") from exc

    expected = str(affiliate_id or "").strip()
    if not expected and not require_attribution:
        return normalized
    if not expected or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", expected):
        raise ValueError("AddLiveTag affiliate_id không hợp lệ.")

    query = parse_qs(urlparse(normalized).query, keep_blank_values=True)
    observed = False
    expected_source = f"an_{expected}"
    for key in AFFILIATE_ID_QUERY_KEYS:
        for value in query.get(key, []):
            value = str(value or "").strip()
            if not value:
                continue
            observed = True
            if value.casefold() != expected.casefold():
                raise AddLiveTagApiError("AddLiveTag trả về link thuộc Affiliate ID khác Brand.")
    for key in AFFILIATE_SOURCE_QUERY_KEYS:
        for value in query.get(key, []):
            value = str(value or "").strip()
            if not value:
                continue
            observed = True
            if value.casefold() != expected_source.casefold():
                raise AddLiveTagApiError("AddLiveTag trả về link thuộc Affiliate ID khác Brand.")
    if require_attribution and not observed:
        raise AddLiveTagApiError("AddLiveTag chưa xác nhận Affiliate ID của Brand trên link Shopee.")
    return normalized


def _generate_api_short_link(origin_url: str, sub_ids: object, *, endpoint: str, timeout: int) -> str:
    sub_id_params = _addlivetag_sub_id_params(sub_ids)
    params = {"originUrl": origin_url, **sub_id_params}
    payload = _request_json_post(
        endpoint,
        {"api_type": "generateShortLink", "params": params},
        timeout=timeout,
        default_timeout=30,
    )
    if sub_id_params and _payload_has_invalid_sub_id(payload):
        # Some AddLiveTag accounts accept the mutation but reject the optional
        # Sub IDs.  Preserve the usable clean link as a fallback; Aurex still
        # stores its original five local tracking dimensions on the link row.
        payload = _request_json_post(
            endpoint,
            {"api_type": "generateShortLink", "params": {"originUrl": origin_url}},
            timeout=timeout,
            default_timeout=30,
        )
    if payload.get("success") is not True:
        raise AddLiveTagApiError(_short_link_error_message(payload))
    link = _extract_short_link(payload)
    if not link:
        raise AddLiveTagApiError(_short_link_error_message(payload))
    return validate_addlivetag_attribution(link, "", require_attribution=False)


def generate_short_link(
    origin_url: str,
    affiliate_id: str,
    sub_ids: object = None,
    *,
    endpoint: str = DEFAULT_SHORT_LINK_URL,
    timeout: int = 30,
    allow_unverified_api: bool = False,
) -> str:
    """Create a Shopee-hosted short link through AddLiveTag's wrapper.

    The default legacy endpoint accepts the Brand Affiliate ID and echoes it
    in the returned URL.  The API-handler trial endpoint is intentionally
    opt-in because its clean slug does not identify which AddLiveTag account
    owns the link.
    """
    endpoint = _validated_endpoint(endpoint, hosts=SHORT_LINK_HOSTS, label="Short-link endpoint")
    endpoint = _canonical_short_link_endpoint(endpoint)
    origin_url = validate_shopee_url(origin_url)
    affiliate_id = str(affiliate_id or "").strip()
    if not affiliate_id:
        raise ValueError("AddLiveTag cần affiliate_id tường minh để tạo short link.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", affiliate_id):
        raise ValueError("AddLiveTag affiliate_id không hợp lệ.")
    if _is_short_link_api_endpoint(endpoint):
        if not allow_unverified_api:
            raise AddLiveTagApiError(
                "AddLiveTag API handler chưa xác nhận đúng Affiliate ID; hãy dùng endpoint /short-link.php."
            )
        return _generate_api_short_link(origin_url, sub_ids, endpoint=endpoint, timeout=timeout)
    payload = _request_json(
        endpoint,
        {"url": origin_url, "aff_id": affiliate_id, **_sub_id_params(sub_ids)},
        timeout=timeout,
        default_timeout=30,
    )
    if payload.get("success") is not True:
        raise AddLiveTagApiError("AddLiveTag không tạo được short link.")
    link = str(payload.get("affiliateLink") or "").strip()
    return validate_addlivetag_attribution(link, affiliate_id, require_attribution=True)
