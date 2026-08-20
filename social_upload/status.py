from __future__ import annotations

from .config import (
    SOCIAL_UPLOAD_CONFIG,
    canonical_brand,
    read_social_config,
    social_brand_route_records,
    social_brand_routes,
    social_brand_routes_version,
    social_config_hint,
    social_platform_connections,
)


def _account_statuses(config: dict, platform: str, status_factory) -> list[dict]:
    """Return sanitized status records for Brand-scoped social accounts."""
    accounts = []
    for connection_id, connection in social_platform_connections(config, platform).items():
        if not isinstance(connection, dict):
            continue
        status = dict(status_factory({**connection, "_brand_connection": True, "_connection_id": str(connection_id)}))
        status["connection_id"] = str(connection_id)
        status["brand"] = canonical_brand(connection.get("brand"))
        status["display_name"] = str(
            connection.get("display_name")
            or connection.get("name")
            or status.get("display_name")
            or connection_id
        ).strip()
        accounts.append(status)
    return accounts


def _with_accounts(status: dict, accounts: list[dict]) -> dict:
    status = dict(status)
    status["accounts"] = accounts
    if accounts:
        for key in ("configured", "connected", "available", "ready"):
            if key in status:
                status[key] = any(bool(account.get(key)) for account in accounts)
        if status.get("connected"):
            status["message"] = ""
    return status
from .binance import (
    binance_config,
    binance_config_hint,
    binance_is_configured,
    disconnect_binance,
)
from .facebook import (
    facebook_active_page_id,
    facebook_config,
    facebook_config_hint,
    facebook_graph_version,
    facebook_is_configured,
    facebook_page_profile,
    facebook_pages_status,
)
from .instagram import (
    instagram_config,
    instagram_status,
)
from .threads import threads_config, threads_status
from .tiktok import zernio_config, zernio_status
from .r2 import r2_config, r2_status
from .youtube import (
    youtube_active_channel_id,
    youtube_config,
    youtube_channels_status,
    youtube_is_configured,
    youtube_is_connected,
    youtube_redirect_uri,
)


def social_status() -> dict:
    config = read_social_config()
    binance = binance_config(config)
    youtube = youtube_config(config)
    facebook = facebook_config(config)
    instagram = instagram_config(config)
    threads = threads_config(config)
    tiktok = zernio_config(config)
    r2 = r2_config(config)
    youtube_channels = youtube_channels_status(config, youtube)
    youtube_channel = next((channel for channel in youtube_channels if channel.get("active")), None)
    facebook_configured = facebook_is_configured(facebook)
    facebook_pages = facebook_pages_status(facebook)
    facebook_page = next((page for page in facebook_pages if page.get("active")), None)
    if not facebook_page and facebook_configured:
        facebook_page = facebook_page_profile(facebook)
    instagram_accounts = _account_statuses(config, "instagram", lambda value: instagram_status(value, r2))
    tiktok_accounts = _account_statuses(config, "tiktok", zernio_status)
    threads_accounts = _account_statuses(config, "threads", threads_status)
    return {
        "config_path": str(SOCIAL_UPLOAD_CONFIG),
        "brand_routes": social_brand_routes(config),
        "brand_route_records": social_brand_route_records(config),
        "brand_routes_version": social_brand_routes_version(config),
        "platforms": {
            "youtube": {
                "configured": youtube_is_configured(youtube),
                "connected": youtube_is_connected(youtube),
                "active_channel_id": youtube_active_channel_id(youtube),
                "channel": youtube_channel or (youtube_channels[0] if youtube_channels else {}),
                "channels": youtube_channels,
                "redirect_uri": youtube_redirect_uri(youtube),
                "message": "" if youtube_is_configured(youtube) else social_config_hint(),
            },
            "facebook": {
                "configured": facebook_configured,
                "connected": facebook_configured,
                "available": facebook_configured,
                "graph_version": facebook_graph_version(facebook),
                "active_page_id": facebook_active_page_id(facebook),
                "page_id": facebook_active_page_id(facebook),
                "page": facebook_page or {},
                "pages": facebook_pages,
                "video_state": str(facebook.get("video_state") or "PUBLISHED").upper(),
                "message": "" if facebook_configured else facebook_config_hint(),
            },
            "instagram": _with_accounts(instagram_status(instagram, r2), instagram_accounts),
            "threads": _with_accounts(threads_status(threads), threads_accounts),
            "tiktok": _with_accounts(zernio_status(tiktok), tiktok_accounts),
            "r2": r2_status(r2),
            "binance": {
                "configured": binance_is_configured(binance),
                "connected": binance_is_configured(binance),
                "available": binance_is_configured(binance),
                "masked": "",
                "message": "" if binance_is_configured(binance) else binance_config_hint(),
            },
        },
    }
