"""Exception types used across the pipeline."""


class Yt2TtError(Exception):
    """Base class for all expected (non-bug) failures."""


class ConfigError(Yt2TtError):
    """Configuration is missing or invalid."""


class SearchError(Yt2TtError):
    """YouTube discovery failed."""


class DownloadError(Yt2TtError):
    """yt-dlp failed to fetch a video."""


class VideoError(Yt2TtError):
    """ffmpeg/ffprobe failed."""


class UploadError(Yt2TtError):
    """TikTok rejected an upload."""


class AuthError(UploadError):
    """TikTok credentials are missing, invalid or expired."""
