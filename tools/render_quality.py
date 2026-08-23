"""Quality profiles shared by the AurexVideo render pipeline.

The renderer produces browser-rasterized frames, so the profile controls both
how those pixels are captured and how the final delivery file is encoded.  The
default is intentionally the quality-first ``master`` profile; callers can
select ``standard`` or ``draft`` when render time or file size matters more.
"""

from __future__ import annotations

from dataclasses import dataclass
import os


RENDER_PROFILE_VERSION = "quality-v2"


@dataclass(frozen=True)
class RenderProfile:
    name: str
    description: str
    capture_format: str
    capture_quality: int | None
    device_scale_factor: float
    encoder_preset: str
    crf: int
    audio_bitrate: str
    pixel_format: str = "yuv420p"
    video_profile: str = "high"
    video_level: str = "4.1"
    allow_frame_reuse: bool = False
    tune: str = "animation"
    gop_seconds: int = 2
    maxrate: str = "20M"
    bufsize: str = "40M"
    reference_frames: int = 3

    @property
    def capture_width_multiplier(self) -> float:
        return self.device_scale_factor

    def encoder_video_options(self, fps: int = 30) -> list[str]:
        """Return delivery-safe video encoder options for FFmpeg."""
        keyint = max(1, round(self.gop_seconds * max(1, int(fps))))
        options = [
            "-c:v", "libx264",
            "-preset", self.encoder_preset,
            "-crf", str(self.crf),
            "-profile:v", self.video_profile,
            "-level:v", self.video_level,
            "-pix_fmt", self.pixel_format,
            "-g", str(keyint),
            "-keyint_min", str(keyint),
            "-sc_threshold", "0",
            "-bf", "2",
            "-x264-params",
            f"keyint={keyint}:min-keyint={keyint}:scenecut=0:open-gop=0:ref={self.reference_frames}",
            "-maxrate", self.maxrate,
            "-bufsize", self.bufsize,
            "-color_range", "tv",
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
        ]
        if self.tune:
            options.extend(["-tune", self.tune])
        return options

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "capture_format": self.capture_format,
            "capture_quality": self.capture_quality,
            "device_scale_factor": self.device_scale_factor,
            "encoder_preset": self.encoder_preset,
            "crf": self.crf,
            "audio_bitrate": self.audio_bitrate,
            "pixel_format": self.pixel_format,
            "video_profile": self.video_profile,
            "video_level": self.video_level,
            "allow_frame_reuse": self.allow_frame_reuse,
            "tune": self.tune,
            "gop_seconds": self.gop_seconds,
            "maxrate": self.maxrate,
            "bufsize": self.bufsize,
            "reference_frames": self.reference_frames,
        }


PROFILES: dict[str, RenderProfile] = {
    "draft": RenderProfile(
        name="draft",
        description="Preview nhanh, dung lượng nhỏ hơn.",
        capture_format="jpeg",
        capture_quality=95,
        device_scale_factor=1.0,
        encoder_preset="veryfast",
        crf=21,
        audio_bitrate="192k",
        allow_frame_reuse=True,
        tune="animation",
        maxrate="12M",
        bufsize="24M",
    ),
    "standard": RenderProfile(
        name="standard",
        description="Chất lượng tốt, tương thích social và cân bằng thời gian.",
        capture_format="png",
        capture_quality=None,
        device_scale_factor=1.0,
        encoder_preset="medium",
        crf=17,
        audio_bitrate="256k",
        allow_frame_reuse=False,
        tune="animation",
        maxrate="20M",
        bufsize="40M",
    ),
    "master": RenderProfile(
        name="master",
        description="Chất lượng cao nhất: capture lossless, supersampling và encode chậm.",
        capture_format="png",
        capture_quality=None,
        device_scale_factor=2.0,
        encoder_preset="slow",
        crf=14,
        audio_bitrate="320k",
        allow_frame_reuse=False,
        tune="animation",
        maxrate="25M",
        bufsize="50M",
    ),
}


def quality_profile_names() -> tuple[str, ...]:
    return tuple(PROFILES.keys())


def get_render_profile(value: str | None = None) -> RenderProfile:
    """Resolve a profile, defaulting to the quality-first master profile."""
    requested = str(value or os.environ.get("AUREXVIDEO_RENDER_PROFILE") or "master").strip().lower()
    aliases = {"best": "master", "high": "master", "fast": "draft"}
    requested = aliases.get(requested, requested)
    try:
        return PROFILES[requested]
    except KeyError as exc:
        allowed = ", ".join(quality_profile_names())
        raise ValueError(f"Quality profile phải là một trong: {allowed}.") from exc
