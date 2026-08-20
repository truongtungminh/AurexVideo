from __future__ import annotations

import json
import re
import time
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import read_social_config, social_brand_route, write_social_config
from .http import http_form_request, http_get_request
from .metadata import (
    build_upload_metadata,
    final_video_path_for_project,
    first_url_from_source,
    read_expected_video_bytes,
    read_project_upload_metadata,
    record_social_upload,
    project_brand_from_topic,
    require_project,
    upload_paragraphs,
)
from .schedule import parse_scheduled_publish_at, scheduled_unix_timestamp, validate_schedule_window


def facebook_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    facebook = config.get("facebook", {}) if isinstance(config, dict) else {}
    if not isinstance(facebook, dict):
        facebook = {}
    return facebook


def facebook_graph_version(facebook: dict) -> str:
    version = str(facebook.get("graph_version") or "v25.0").strip()
    return version if version.startswith("v") else f"v{version}"


def facebook_pages(facebook: dict) -> list[dict]:
    raw_pages = facebook.get("pages")
    pages = raw_pages if isinstance(raw_pages, list) else []
    normalized = []
    seen = set()
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("id") or page.get("page_id") or "").strip()
        page_access_token = str(page.get("page_access_token") or page.get("access_token") or "").strip()
        if not page_id or not page_access_token or page_id in seen:
            continue
        seen.add(page_id)
        normalized.append({
            "id": page_id,
            "page_access_token": page_access_token,
            "name": str(page.get("name") or "").strip(),
            "thumbnail": str(page.get("thumbnail") or "").strip(),
        })
    legacy_page_id = str(facebook.get("page_id") or "").strip()
    legacy_page_access_token = str(facebook.get("page_access_token") or "").strip()
    if legacy_page_id and legacy_page_access_token and legacy_page_id not in seen:
        normalized.append({
            "id": legacy_page_id,
            "page_access_token": legacy_page_access_token,
            "name": str(facebook.get("page_name") or "").strip(),
            "thumbnail": str(facebook.get("page_thumbnail") or "").strip(),
        })
    return normalized


def facebook_active_page_id(facebook: dict) -> str:
    active_page_id = str(facebook.get("active_page_id") or facebook.get("page_id") or "").strip()
    if active_page_id:
        return active_page_id
    pages = facebook_pages(facebook)
    return str(pages[0].get("id") or "") if pages else ""


def facebook_active_page(facebook: dict) -> dict:
    pages = facebook_pages(facebook)
    active_page_id = facebook_active_page_id(facebook)
    for page in pages:
        if page.get("id") == active_page_id:
            return page
    return pages[0] if pages else {}


def facebook_page_for_id(facebook: dict, page_id: str) -> dict:
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ValueError("Facebook page id is empty.")
    for page in facebook_pages(facebook):
        if str(page.get("id") or "").strip() == page_id:
            return page
    raise ValueError(f"Facebook Page {page_id} is not configured in facebook.pages.")


def facebook_page_id(facebook: dict, page: dict | None = None) -> str:
    return str((page or facebook_active_page(facebook)).get("id") or "").strip()


def facebook_page_access_token(facebook: dict, page: dict | None = None) -> str:
    return str((page or facebook_active_page(facebook)).get("page_access_token") or "").strip()


def facebook_is_configured(facebook: dict) -> bool:
    return bool(facebook_page_id(facebook) and facebook_page_access_token(facebook))


def facebook_config_hint() -> str:
    return "Facebook chưa cấu hình. Bấm Thêm Page để nhập Page ID và Page access token."


def facebook_reels_url(facebook: dict, page: dict | None = None) -> str:
    page_id = facebook_page_id(facebook, page)
    return f"https://graph.facebook.com/{facebook_graph_version(facebook)}/{quote(page_id, safe='')}/video_reels"


def facebook_post_comment_url(facebook: dict, post_id: str) -> str:
    return f"https://graph.facebook.com/{facebook_graph_version(facebook)}/{quote(post_id, safe='')}/comments"


def facebook_full_post_id(facebook: dict, object_id: str, page: dict | None = None) -> str:
    object_id = str(object_id or "").strip()
    if not object_id or "_" in object_id:
        return object_id
    page_id = facebook_page_id(facebook, page)
    return f"{page_id}_{object_id}" if page_id else object_id


def facebook_upload_page(config: dict, facebook: dict, payload: dict) -> dict:
    brand = str(payload.get("brand") or "").strip().casefold()
    requested_id = str(payload.get("pageId") or payload.get("page_id") or "").strip()
    if not brand:
        raise ValueError("Facebook upload thiếu brand; không được dùng active Page mặc định.")
    route = social_brand_route(config, brand, "facebook")
    expected_id = route["page_id"]
    if requested_id and requested_id != expected_id:
        raise ValueError(f"Facebook Page không khớp route của brand {brand}.")
    return facebook_page_for_id(facebook, expected_id)


def facebook_page_profile(facebook: dict, page: dict | None = None) -> dict:
    active_page = page or facebook_active_page(facebook)
    page_id = str(active_page.get("id") or "").strip()
    access_token = str(active_page.get("page_access_token") or "").strip()
    profile = {
        "id": page_id,
        "name": str(active_page.get("name") or "").strip(),
        "thumbnail": str(active_page.get("thumbnail") or "").strip(),
    }
    if not page_id or not access_token:
        return profile
    try:
        data = http_get_request(
            f"https://graph.facebook.com/{facebook_graph_version(facebook)}/{quote(page_id, safe='')}",
            {
                "fields": "id,name,picture{url}",
                "access_token": access_token,
            },
        )
    except RuntimeError as exc:
        profile["error"] = str(exc)
        return profile
    picture = data.get("picture", {}) if isinstance(data.get("picture"), dict) else {}
    picture_data = picture.get("data", {}) if isinstance(picture.get("data"), dict) else {}
    profile.update({
        "id": str(data.get("id") or page_id),
        "name": str(data.get("name") or profile["name"]),
        "thumbnail": str(picture_data.get("url") or profile["thumbnail"]),
    })
    return profile


def facebook_pages_status(facebook: dict) -> list[dict]:
    active_page_id = facebook_active_page_id(facebook)
    result = []
    for page in facebook_pages(facebook):
        profile = facebook_page_profile(facebook, page)
        profile["active"] = profile.get("id") == active_page_id
        result.append(profile)
    return result


def set_facebook_active_page(page_id: str) -> dict:
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ValueError("Missing Facebook page id.")
    config = read_social_config()
    facebook = facebook_config(config)
    pages = facebook_pages(facebook)
    if not any(page.get("id") == page_id for page in pages):
        raise ValueError("Facebook page id is not configured in facebook.pages.")
    facebook["active_page_id"] = page_id
    config["facebook"] = facebook
    write_social_config(config)
    return {"ok": True, "active_page_id": page_id}


def disconnect_facebook_page(page_id: str = "") -> dict:
    """Remove one configured page, or every page when no id is given."""
    page_id = str(page_id or "").strip()
    config = read_social_config()
    facebook = facebook_config(config)
    pages = facebook_pages(facebook)
    if page_id:
        remaining = [page for page in pages if str(page.get("id") or "") != page_id]
        if len(remaining) == len(pages):
            raise ValueError("Facebook page id is not configured in facebook.pages.")
    else:
        remaining = []
    facebook["pages"] = remaining
    if str(facebook.get("active_page_id") or "") not in {str(page.get("id") or "") for page in remaining}:
        facebook["active_page_id"] = str(remaining[0].get("id") or "") if remaining else ""
    config["facebook"] = facebook
    write_social_config(config)
    return {"ok": True, "removed": page_id or "all", "pages": [str(page.get("id") or "") for page in remaining]}


def update_facebook_page_config(page_id: str, page_access_token: str) -> dict:
    page_id = str(page_id or "").strip()
    page_access_token = str(page_access_token or "").strip()
    if not page_id:
        raise ValueError("Missing Facebook page id.")
    if not page_access_token:
        raise ValueError("Missing Facebook page access token.")
    if not re.fullmatch(r"[0-9]+", page_id):
        raise ValueError("Facebook page id should contain digits only.")
    if len(page_access_token) < 20 or re.search(r"\s", page_access_token):
        raise ValueError("Facebook page access token looks invalid.")

    config = read_social_config()
    facebook = facebook_config(config)
    pages = facebook_pages(facebook)
    next_page = {
        "id": page_id,
        "page_access_token": page_access_token,
    }
    replaced = False
    for index, page in enumerate(pages):
        if page.get("id") == page_id:
            next_page["name"] = str(page.get("name") or "").strip()
            next_page["thumbnail"] = str(page.get("thumbnail") or "").strip()
            pages[index] = next_page
            replaced = True
            break
    if not replaced:
        pages.append(next_page)

    facebook["pages"] = pages
    facebook["active_page_id"] = page_id
    facebook.setdefault("graph_version", "v25.0")
    facebook.setdefault("video_state", "PUBLISHED")
    config["facebook"] = facebook
    write_social_config(config)
    return {
        "ok": True,
        "active_page_id": page_id,
        "configured": True,
    }


def facebook_graph_object_url(facebook: dict, object_id: str) -> str:
    return f"https://graph.facebook.com/{facebook_graph_version(facebook)}/{quote(object_id, safe='')}"


def facebook_object_metadata(facebook: dict, object_id: str, access_token: str, fields: str = "id") -> dict:
    if not object_id:
        return {}
    return http_get_request(
        facebook_graph_object_url(facebook, object_id),
        {
            "fields": fields,
            "access_token": access_token,
        },
    )


def wait_for_facebook_object_ready(
    facebook: dict,
    object_id: str,
    access_token: str,
    attempts: int = 8,
    delay_seconds: float = 4.0,
) -> tuple[dict, str]:
    last_error = ""
    for attempt in range(attempts):
        try:
            data = facebook_object_metadata(facebook, object_id, access_token)
            if str(data.get("id") or "").strip():
                return data, ""
            last_error = f"Facebook object response did not include an id: {data}"
        except RuntimeError as exc:
            last_error = str(exc)
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    return {}, last_error


def post_facebook_source_comment(facebook: dict, object_id: str, message: str, access_token: str) -> tuple[str, str]:
    last_error = ""
    for attempt in range(4):
        try:
            comment_data = http_form_request(
                facebook_post_comment_url(facebook, object_id),
                {
                    "message": message,
                    "access_token": access_token,
                },
            )
            comment_id = str(comment_data.get("id") or "").strip()
            if comment_id:
                return comment_id, ""
            last_error = f"Facebook comment response did not include an id: {comment_data}"
        except RuntimeError as exc:
            last_error = str(exc)
        if attempt < 3:
            time.sleep(3)
    return "", last_error


def facebook_caption_for_project(project: str, fallback_caption: str) -> tuple[str, str]:
    project_dir = require_project(project)
    script_path = project_dir / "script.txt"
    script_lines = [line.strip() for line in script_path.read_text(encoding="utf-8").splitlines() if line.strip()] if script_path.exists() else []
    source_url = first_url_from_source(project_dir)
    caption = str(fallback_caption or "").strip() or upload_paragraphs(script_lines).strip()
    caption = re.sub(r"(?im)^\s*Nguồn:\s*https?://\S+\s*$", "", caption)
    if source_url:
        caption = caption.replace(source_url, "")
    caption = re.sub(r"\n{3,}", "\n\n", caption).strip() or upload_paragraphs(script_lines).strip()
    return caption[:5000], source_url


def facebook_upload_video(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    video_path = final_video_path_for_project(project)
    project_brand = project_brand_from_topic(video_path.parent.parent)
    declared_brand = str(payload.get("brand") or "").strip().casefold()
    brand = declared_brand or project_brand
    if not brand:
        raise ValueError("Chưa chọn brand; upload Facebook bị chặn để tránh dùng nhầm Page.")
    metadata = build_upload_metadata(project)
    caption, source_url = facebook_caption_for_project(
        project,
        str(payload.get("facebookCaption") or metadata["facebookCaption"]),
    )

    config = read_social_config()
    facebook = facebook_config(config)
    upload_payload = dict(payload)
    upload_payload["brand"] = brand
    upload_payload["pageId"] = social_brand_route(config, brand, "facebook")["page_id"]
    page = facebook_upload_page(config, facebook, upload_payload)
    if not facebook_page_id(facebook, page) or not facebook_page_access_token(facebook, page):
        raise ValueError(facebook_config_hint())
    access_token = facebook_page_access_token(facebook, page)
    video_bytes = read_expected_video_bytes(video_path, payload)
    video_state = str(payload.get("facebookVideoState") or metadata.get("facebookVideoState") or facebook.get("video_state") or "PUBLISHED").strip().upper()
    if video_state not in {"DRAFT", "PUBLISHED", "SCHEDULED"}:
        raise ValueError("facebook.video_state must be DRAFT, PUBLISHED, or SCHEDULED.")
    scheduled_publish_at = parse_scheduled_publish_at(payload)
    if not scheduled_publish_at:
        stored = read_project_upload_metadata(project)
        scheduled_publish_at = parse_scheduled_publish_at(stored.get('facebook', {}) if isinstance(stored, dict) else {})
    if scheduled_publish_at:
        # Facebook Graph API schedules Reels via video_state=SCHEDULED plus
        # scheduled_publish_time; the window is 10 minutes to 75 days ahead.
        validate_schedule_window(
            scheduled_publish_at,
            timedelta(minutes=10),
            max_ahead=timedelta(days=75),
            platform="Facebook",
        )
        video_state = "SCHEDULED"
    elif video_state == "SCHEDULED":
        raise ValueError("Hẹn giờ đăng Facebook cần thời gian đăng (scheduledPublishAt).")

    reels_url = facebook_reels_url(facebook, page)
    start_data = http_form_request(
        reels_url,
        {
            "upload_phase": "start",
            "access_token": access_token,
        },
    )
    video_id = str(start_data.get("video_id") or "").strip()
    if not video_id:
        raise RuntimeError(f"Facebook upload start did not return a video_id: {start_data}")
    upload_url = str(start_data.get("upload_url") or "").strip()
    if not upload_url:
        upload_url = f"https://rupload.facebook.com/video-upload/{facebook_graph_version(facebook)}/{quote(video_id, safe='')}"

    file_size = len(video_bytes)
    upload_request = Request(
        upload_url,
        data=video_bytes,
        headers={
            "Authorization": f"OAuth {access_token}",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(file_size),
            "offset": "0",
            "file_size": str(file_size),
        },
        method="POST",
    )
    try:
        with urlopen(upload_request, timeout=600) as response:
            upload_text = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Facebook video upload failed: HTTP {exc.code}: {detail}") from exc
    upload_data = json.loads(upload_text) if upload_text.strip() else {}
    if upload_data.get("success") is False:
        raise RuntimeError(f"Facebook video upload failed: {upload_data}")

    finish_fields = {
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": video_state,
        "description": caption,
        "access_token": access_token,
    }
    if scheduled_publish_at:
        finish_fields["scheduled_publish_time"] = str(scheduled_unix_timestamp(scheduled_publish_at))
    finish_data = http_form_request(reels_url, finish_fields)
    if finish_data.get("success") is False:
        raise RuntimeError(f"Facebook upload finish failed: {finish_data}")

    post_id = str(finish_data.get("post_id") or finish_data.get("id") or "").strip()
    comment_target_id = facebook_full_post_id(facebook, post_id or video_id, page)
    permalink_url = str(finish_data.get("permalink_url") or "").strip()

    reel_url = permalink_url or (f"https://www.facebook.com/reel/{video_id}" if video_id and video_state == "PUBLISHED" else "")
    record_social_upload(
        project,
        "facebook",
        {
            "url": reel_url,
            "video_id": video_id,
            "post_id": post_id,
            "brand": brand,
            "state": video_state,
            "scheduled_at": scheduled_publish_at or "",
        },
    )
    if video_state == "SCHEDULED":
        message = (
            f"Đã lên lịch đăng Facebook Reels lúc {scheduled_publish_at}. "
            "Video sẽ tự đăng đúng giờ — xem trong Meta Business Suite → Lịch đăng bài nếu cần chỉnh."
        )
    else:
        message = (
            f"Uploaded to Facebook Reels as {video_state}. Use Comment source after publish to add the source link." if video_state == "PUBLISHED" else
            f"Uploaded to Facebook Reels as {video_state}. Review in Meta Business Suite/Page before publishing if needed."
        )
    return {
        "ok": True,
        "platform": "facebook",
        "project": project,
        "brand": brand,
        "page_id": facebook_page_id(facebook, page),
        "video_id": video_id,
        "post_id": post_id,
        "url": reel_url,
        "source_url": source_url,
        "source_comment_target_id": comment_target_id if video_state == "PUBLISHED" else "",
        "source_comment_id": "",
        "source_comment_error": "",
        "video_state": video_state,
        "scheduledPublishAt": scheduled_publish_at or "",
        "message": message,
    }


def facebook_comment_source(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    metadata = build_upload_metadata(project) if project else {}
    source_comment = str(payload.get("facebookSourceComment") or metadata.get("facebookSourceComment") or "").strip()
    if not source_comment:
        raise ValueError("Source comment is empty.")

    target_id = str(
        payload.get("sourceCommentTargetId")
        or payload.get("source_comment_target_id")
        or payload.get("postId")
        or payload.get("post_id")
        or payload.get("videoId")
        or payload.get("video_id")
        or ""
    ).strip()
    if not target_id:
        raise ValueError("Missing Facebook Reel/Post id. Upload Facebook Reels as PUBLISHED first, then click Comment source.")

    config = read_social_config()
    facebook = facebook_config(config)
    if not facebook_is_configured(facebook):
        raise ValueError(facebook_config_hint())

    access_token = facebook_page_access_token(facebook)
    target_id = facebook_full_post_id(facebook, target_id)
    comment_id, comment_error = post_facebook_source_comment(facebook, target_id, source_comment, access_token)
    if not comment_id:
        raise RuntimeError(f"Facebook source comment failed: {comment_error}")

    return {
        "ok": True,
        "platform": "facebook",
        "project": project,
        "source_comment_target_id": target_id,
        "source_comment_id": comment_id,
        "message": "Source link was posted as a Facebook comment.",
    }
