from __future__ import annotations

from typing import Callable

from .facebook import facebook_comment_source, facebook_upload_video
from .instagram import instagram_upload_video
from .threads import threads_upload_video


PLATFORMS = ("instagram", "facebook", "threads")


def _error_result(platform: str, exc: Exception) -> dict:
    message = str(exc).strip() or f"{platform} upload failed."
    return {
        "ok": False,
        "platform": platform,
        "error": message,
        "message": f"{platform.capitalize()}: {message}",
    }


def _run_platform(platform: str, operation: Callable[[], dict]) -> dict:
    try:
        result = operation()
    except Exception as exc:
        return _error_result(platform, exc)
    return {"ok": True, "platform": platform, **(result if isinstance(result, dict) else {})}


def publish_instagram_facebook_threads(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    if not project:
        raise ValueError("Missing project.")
    facebook_video_state = str(payload.get("facebookVideoState") or "PUBLISHED").strip().upper()
    if facebook_video_state != "PUBLISHED":
        raise ValueError("Đăng chung yêu cầu Facebook ở trạng thái PUBLISHED.")

    instagram_payload = {
        "project": project,
        "instagramCaption": str(payload.get("instagramCaption") or "").strip(),
    }
    facebook_payload = {
        "project": project,
        "facebookCaption": str(payload.get("facebookCaption") or "").strip(),
        "facebookVideoState": facebook_video_state,
    }
    threads_payload = {
        "project": project,
        "threadsText": str(
            payload.get("threadsText")
            or payload.get("threadsCaption")
            or payload.get("instagramCaption")
            or payload.get("facebookCaption")
            or ""
        ).strip(),
    }
    brand = str(payload.get("brand") or payload.get("brandId") or "").strip().casefold()
    if brand:
        instagram_payload["brand"] = brand
        facebook_payload["brand"] = brand
        threads_payload["brand"] = brand

    results = {
        "instagram": _run_platform("instagram", lambda: instagram_upload_video(instagram_payload)),
        "facebook": _run_platform("facebook", lambda: facebook_upload_video(facebook_payload)),
        "threads": _run_platform("threads", lambda: threads_upload_video(threads_payload)),
    }

    source_comment = str(payload.get("facebookSourceComment") or "").strip()
    facebook_result = results["facebook"]
    if facebook_result.get("ok") and source_comment:
        target_id = str(facebook_result.get("source_comment_target_id") or "").strip()
        if target_id:
            results["facebook_comment"] = _run_platform(
                "facebook comment",
                lambda: facebook_comment_source({
                    "project": project,
                    "sourceCommentTargetId": target_id,
                    "facebookSourceComment": source_comment,
                }),
            )
        else:
            results["facebook_comment"] = _error_result(
                "facebook comment",
                RuntimeError("Facebook upload did not return a Reel/Post ID for source comment."),
            )

    successful = [platform for platform in PLATFORMS if results[platform].get("ok")]
    failed = [platform for platform in PLATFORMS if not results[platform].get("ok")]
    if results.get("facebook_comment") and not results["facebook_comment"].get("ok"):
        failed.append("facebook_comment")
    all_ok = not failed
    partial = bool(successful and failed)
    failure_labels = {"facebook_comment": "Facebook comment"}
    if all_ok:
        message = "Đã đăng Instagram, Facebook và Threads."
    elif partial:
        message = (
            f"Đã đăng một phần: {', '.join(successful)}; lỗi: "
            f"{', '.join(failure_labels.get(platform, platform) for platform in failed)}."
        )
    else:
        message = "Cả ba nền tảng đều đăng thất bại."
    return {
        "ok": all_ok,
        "partial": partial,
        "project": project,
        "platforms": results,
        "successful": successful,
        "failed": failed,
        "message": message,
    }
