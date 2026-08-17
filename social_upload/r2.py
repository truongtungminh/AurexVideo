from __future__ import annotations

import hashlib
import hmac
import http.client
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from .config import read_social_config, write_social_config


R2_REGION = "auto"
R2_ENDPOINT_SUFFIX = ".r2.cloudflarestorage.com"
R2_CONFIG_ENV = {
    "account_id": "CLOUDFLARE_R2_ACCOUNT_ID",
    "bucket": "CLOUDFLARE_R2_BUCKET",
    "access_key_id": "CLOUDFLARE_R2_ACCESS_KEY_ID",
    "secret_access_key": "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
    "public_base_url": "CLOUDFLARE_R2_PUBLIC_BASE_URL",
    "region": "CLOUDFLARE_R2_REGION",
    "object_prefix": "CLOUDFLARE_R2_OBJECT_PREFIX",
}


def r2_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    value = config.get("r2", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def resolve_r2_config(r2: dict | None = None) -> dict:
    saved = r2 if isinstance(r2, dict) else r2_config()
    result = {}
    for key, env_name in R2_CONFIG_ENV.items():
        result[key] = str(os.environ.get(env_name) or saved.get(key) or "").strip()
    result["region"] = result["region"] or R2_REGION
    result["object_prefix"] = result["object_prefix"].strip("/") or "instagram"
    result["retain_media"] = bool(saved.get("retain_media"))
    return result


def r2_is_configured(r2: dict | None = None) -> bool:
    value = resolve_r2_config(r2)
    required = ("account_id", "bucket", "access_key_id", "secret_access_key", "public_base_url")
    return all(value.get(key) for key in required)


def r2_config_hint() -> str:
    return "Cloudflare R2 chưa cấu hình. Nhập Account ID, bucket, Access Key, Secret Key và public URL."


def r2_mask_access_key(r2: dict | None = None) -> str:
    value = resolve_r2_config(r2).get("access_key_id", "")
    if len(value) <= 8:
        return f"{value[:2]}..." if value else ""
    return f"{value[:4]}...{value[-4:]}"


def r2_public_url(key: str, r2: dict | None = None) -> str:
    config = resolve_r2_config(r2)
    base = config.get("public_base_url", "").rstrip("/")
    if not base:
        raise ValueError(r2_config_hint())
    return f"{base}/{quote(str(key).lstrip('/'), safe='/-_.~')}"


def update_r2_config(
    account_id: str,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    public_base_url: str,
    region: str = R2_REGION,
    object_prefix: str = "instagram",
    retain_media: bool = False,
    *,
    config: dict | None = None,
    persist: bool = True,
) -> dict:
    values = {
        "account_id": str(account_id or "").strip(),
        "bucket": str(bucket or "").strip(),
        "access_key_id": str(access_key_id or "").strip(),
        "secret_access_key": str(secret_access_key or "").strip(),
        "public_base_url": str(public_base_url or "").strip().rstrip("/"),
        "region": str(region or R2_REGION).strip() or R2_REGION,
        "object_prefix": str(object_prefix or "instagram").strip("/") or "instagram",
        "retain_media": bool(retain_media),
    }
    _validate_r2_config(values)
    config = read_social_config() if config is None else config
    config["r2"] = values
    if persist:
        write_social_config(config)
    return {
        "ok": True,
        "configured": True,
        "bucket": values["bucket"],
        "public_base_url": values["public_base_url"],
        "region": values["region"],
        "object_prefix": values["object_prefix"],
        "retain_media": values["retain_media"],
        "masked_access_key": r2_mask_access_key(values),
    }


def disconnect_r2() -> dict:
    config = read_social_config()
    config.pop("r2", None)
    write_social_config(config)
    return {"ok": True, "configured": False}


def r2_status(r2: dict | None = None) -> dict:
    config = resolve_r2_config(r2)
    configured = r2_is_configured(r2)
    return {
        "configured": configured,
        "connected": configured,
        "bucket": config.get("bucket", ""),
        "public_base_url": config.get("public_base_url", ""),
        "region": config.get("region", R2_REGION),
        "object_prefix": config.get("object_prefix", "instagram"),
        "retain_media": bool(config.get("retain_media")),
        "masked_access_key": r2_mask_access_key(r2),
        "message": "" if configured else r2_config_hint(),
    }


def _validate_r2_config(config: dict) -> None:
    required = {
        "account_id": "Cloudflare R2 Account ID",
        "bucket": "Cloudflare R2 bucket",
        "access_key_id": "Cloudflare R2 Access Key ID",
        "secret_access_key": "Cloudflare R2 Secret Access Key",
        "public_base_url": "Cloudflare R2 public base URL",
    }
    for key, label in required.items():
        if not str(config.get(key) or "").strip():
            raise ValueError(f"{label} không được để trống.")
    if not str(config["public_base_url"]).startswith(("https://", "http://")):
        raise ValueError("R2 public base URL phải bắt đầu bằng https:// hoặc http://.")
    if "/" in str(config["bucket"]).strip() or "\\" in str(config["bucket"]).strip():
        raise ValueError("Tên R2 bucket không được chứa dấu '/'.")


def _r2_endpoint(config: dict) -> tuple[str, str, str]:
    account_id = str(config.get("account_id") or "").strip()
    bucket = str(config.get("bucket") or "").strip()
    if not account_id or not bucket:
        raise ValueError(r2_config_hint())
    host = f"{account_id}{R2_ENDPOINT_SUFFIX}"
    return f"https://{host}", host, bucket


def _canonical_uri(bucket: str, key: str) -> str:
    return f"/{quote(bucket, safe='-_.~')}/{quote(str(key).lstrip('/'), safe='/-_.~')}"


def _sign(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str = "s3") -> bytes:
    date_key = _sign(f"AWS4{secret}".encode("utf-8"), date_stamp)
    region_key = _sign(date_key, region)
    service_key = _sign(region_key, service)
    return _sign(service_key, "aws4_request")


def _payload_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signed_headers(method: str, canonical_uri: str, host: str, payload_hash: str, config: dict) -> dict:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region = str(config.get("region") or R2_REGION)
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        (method, canonical_uri, "", canonical_headers, signed_headers, payload_hash)
    )
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", amz_date, credential_scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest())
    )
    signature = hmac.new(
        _signing_key(str(config["secret_access_key"]), date_stamp, region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={config['access_key_id']}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


def _open_connection(endpoint: str, host: str, timeout: int) -> http.client.HTTPSConnection:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Cloudflare R2 endpoint must use HTTPS.")
    return http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)


def upload_file(
    file_path: Path,
    key: str,
    content_type: str = "application/octet-stream",
    r2: dict | None = None,
    timeout: int = 600,
) -> str:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"R2 upload source file not found: {path}")
    config = resolve_r2_config(r2)
    _validate_r2_config(config)
    endpoint, host, bucket = _r2_endpoint(config)
    object_key = str(key).strip().lstrip("/")
    if not object_key:
        raise ValueError("R2 object key không được để trống.")
    payload_hash = _payload_sha256(path)
    canonical_uri = _canonical_uri(bucket, object_key)
    headers = _signed_headers("PUT", canonical_uri, host, payload_hash, config)
    headers.update({
        "Content-Type": content_type,
        "Content-Length": str(path.stat().st_size),
        "Cache-Control": "public, max-age=3600",
    })
    connection = _open_connection(endpoint, host, timeout)
    try:
        connection.putrequest("PUT", canonical_uri, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                connection.send(chunk)
        response = connection.getresponse()
        detail = response.read().decode("utf-8", "replace")
        if response.status >= 300:
            raise RuntimeError(f"Cloudflare R2 upload failed: HTTP {response.status}: {detail[:500]}")
    except OSError as exc:
        raise RuntimeError(f"Cloudflare R2 upload failed: {exc}") from exc
    finally:
        connection.close()
    return r2_public_url(object_key, config)


def delete_file(key: str, r2: dict | None = None, timeout: int = 120) -> None:
    config = resolve_r2_config(r2)
    _validate_r2_config(config)
    endpoint, host, bucket = _r2_endpoint(config)
    object_key = str(key).strip().lstrip("/")
    if not object_key:
        return
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_uri = _canonical_uri(bucket, object_key)
    headers = _signed_headers("DELETE", canonical_uri, host, payload_hash, config)
    headers["Content-Length"] = "0"
    connection = _open_connection(endpoint, host, timeout)
    try:
        connection.putrequest("DELETE", canonical_uri, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        detail = response.read().decode("utf-8", "replace")
        if response.status >= 300:
            raise RuntimeError(f"Cloudflare R2 delete failed: HTTP {response.status}: {detail[:500]}")
    except OSError as exc:
        raise RuntimeError(f"Cloudflare R2 delete failed: {exc}") from exc
    finally:
        connection.close()
