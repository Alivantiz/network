"""Configuration loading: YAML file + environment overrides.

Secrets never live in the YAML file — they come from the environment (or a
local ``.env``), so a config can be committed without leaking credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

ENV_PREFIX_MAP = {
    "youtube_api_key": "YOUTUBE_API_KEY",
    "client_key": "TIKTOK_CLIENT_KEY",
    "client_secret": "TIKTOK_CLIENT_SECRET",
    "refresh_token": "TIKTOK_REFRESH_TOKEN",
    "access_token": "TIKTOK_ACCESS_TOKEN",
}


def load_dotenv(path: Path) -> None:
    """Load ``KEY=value`` pairs from *path* into os.environ (no overwrite)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class SearchConfig:
    queries: list[str] = field(
        default_factory=lambda: ["китайская дорама", "chinese drama eng sub"]
    )
    channels: list[str] = field(default_factory=list)
    playlists: list[str] = field(default_factory=list)
    backend: str = "auto"  # api | ytdlp | auto
    max_results: int = 25
    region_code: str = "RU"
    relevance_language: str = "ru"
    published_within_days: int = 90
    min_duration_sec: int = 240
    max_duration_sec: int = 7200
    require_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(
        default_factory=lambda: ["trailer", "трейлер", "reaction", "обзор"]
    )
    exclude_channels: list[str] = field(default_factory=list)
    youtube_api_key: str | None = None


@dataclass
class DownloadConfig:
    dir: str = "work/downloads"
    format: str = "bv*[height<=1080]+ba/b[height<=1080]"
    cookies_file: str | None = None
    rate_limit: str | None = None
    max_filesize_mb: int = 4096
    retries: int = 3


@dataclass
class ClipConfig:
    dir: str = "work/clips"
    part_seconds: int = 60
    min_part_seconds: int = 20
    skip_intro_seconds: int = 0
    skip_outro_seconds: int = 0
    max_parts: int = 10
    orientation: str = "blur"  # blur | crop | pad | none
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 23
    preset: str = "veryfast"
    audio_bitrate: str = "128k"


@dataclass
class MetadataConfig:
    title_template: str = "{title} — часть {index}/{total}"
    hashtags: list[str] = field(default_factory=lambda: ["#дорама", "#cdrama", "#сериал"])
    max_title_length: int = 2200


@dataclass
class TikTokConfig:
    mode: str = "inbox"  # inbox (draft in the app) | direct (publish immediately)
    privacy_level: str = "SELF_ONLY"
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    video_cover_timestamp_ms: int = 1000
    chunk_size_mb: int = 10
    post_interval_seconds: int = 900
    daily_limit: int = 10
    poll_timeout_seconds: int = 600
    client_key: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    access_token: str | None = None


@dataclass
class RuntimeConfig:
    state_db: str = "work/state.db"
    log_file: str | None = "work/yt2tt.log"
    dry_run: bool = False
    keep_source: bool = False
    keep_clips: bool = True
    max_videos_per_run: int = 1


@dataclass
class Config:
    search: SearchConfig = field(default_factory=SearchConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    clip: ClipConfig = field(default_factory=ClipConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    tiktok: TikTokConfig = field(default_factory=TikTokConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    # ---- construction -------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None, env_file: str | Path = ".env") -> Config:
        load_dotenv(Path(env_file))
        data: dict[str, Any] = {}
        if path:
            p = Path(path)
            if not p.is_file():
                raise ConfigError(f"config file not found: {p}")
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ConfigError(f"config file must contain a mapping: {p}")
        cfg = cls.from_dict(data)
        cfg.apply_env()
        return cfg

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        kwargs: dict[str, Any] = {}
        known = {f.name: f for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                raise ConfigError(f"unknown config section: {key!r}")
            section_cls = _SECTIONS[key]
            if not isinstance(value, dict):
                raise ConfigError(f"section {key!r} must be a mapping, got {type(value).__name__}")
            kwargs[key] = _build_section(section_cls, value, key)
        return cls(**kwargs)

    def apply_env(self) -> None:
        """Overlay secrets from the environment onto the loaded config."""
        for section in (self.search, self.tiktok):
            for f in fields(section):
                env_name = ENV_PREFIX_MAP.get(f.name)
                if env_name and os.environ.get(env_name):
                    setattr(section, f.name, os.environ[env_name])

    # ---- validation ---------------------------------------------------
    def validate(self) -> None:
        c = self.clip
        if c.part_seconds <= 0:
            raise ConfigError("clip.part_seconds must be > 0")
        if c.min_part_seconds < 0 or c.min_part_seconds > c.part_seconds:
            raise ConfigError("clip.min_part_seconds must be between 0 and clip.part_seconds")
        if c.orientation not in {"blur", "crop", "pad", "none"}:
            raise ConfigError("clip.orientation must be one of blur|crop|pad|none")
        if c.max_parts <= 0:
            raise ConfigError("clip.max_parts must be > 0")
        if self.search.backend not in {"auto", "api", "ytdlp"}:
            raise ConfigError("search.backend must be one of auto|api|ytdlp")
        if not (self.search.queries or self.search.channels or self.search.playlists):
            raise ConfigError("search needs at least one of: queries, channels, playlists")
        t = self.tiktok
        if t.mode not in {"inbox", "direct"}:
            raise ConfigError("tiktok.mode must be inbox or direct")
        if not 5 <= t.chunk_size_mb <= 64:
            raise ConfigError("tiktok.chunk_size_mb must be between 5 and 64 (TikTok limit)")
        if t.privacy_level not in {
            "PUBLIC_TO_EVERYONE",
            "MUTUAL_FOLLOW_FRIENDS",
            "FOLLOWER_OF_CREATOR",
            "SELF_ONLY",
        }:
            raise ConfigError(f"tiktok.privacy_level is not a valid value: {t.privacy_level!r}")

    def require_tiktok_credentials(self) -> None:
        missing = [
            name
            for name in ("client_key", "client_secret")
            if not getattr(self.tiktok, name)
        ]
        if missing:
            raise ConfigError(
                "missing TikTok credentials: "
                + ", ".join(ENV_PREFIX_MAP[m] for m in missing)
                + " (see .env.example)"
            )
        if not (self.tiktok.refresh_token or self.tiktok.access_token):
            raise ConfigError(
                "set TIKTOK_REFRESH_TOKEN (preferred) or TIKTOK_ACCESS_TOKEN in the environment"
            )


_SECTIONS: dict[str, type] = {
    "search": SearchConfig,
    "download": DownloadConfig,
    "clip": ClipConfig,
    "metadata": MetadataConfig,
    "tiktok": TikTokConfig,
    "runtime": RuntimeConfig,
}


def _build_section(section_cls: type, value: dict[str, Any], name: str):
    if not is_dataclass(section_cls):  # pragma: no cover - defensive
        raise ConfigError(f"bad section type for {name}")
    known = {f.name for f in fields(section_cls)}
    unknown = set(value) - known
    if unknown:
        raise ConfigError(f"unknown keys in section {name!r}: {', '.join(sorted(unknown))}")
    return section_cls(**value)
