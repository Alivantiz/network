"""YouTube discovery.

Two interchangeable backends:

* ``api``   — YouTube Data API v3 (needs ``YOUTUBE_API_KEY``); precise filters,
              quota-limited.
* ``ytdlp`` — scraping through yt-dlp; no key, no quota, slightly less metadata.

``auto`` picks the API when a key is present and falls back to yt-dlp.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import SearchConfig
from .errors import SearchError

log = logging.getLogger("yt2tt.youtube")

API_ROOT = "https://www.googleapis.com/youtube/v3"
_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


@dataclass
class VideoCandidate:
    video_id: str
    title: str
    url: str
    channel: str | None = None
    channel_id: str | None = None
    duration: int | None = None
    published_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "channel": self.channel,
            "duration": self.duration,
            "published_at": self.published_at,
        }


def parse_iso8601_duration(value: str) -> int:
    """Convert an ISO-8601 duration (``PT1H2M3S``) to whole seconds."""
    match = _ISO_DURATION.match(value or "")
    if not match:
        raise ValueError(f"unparseable duration: {value!r}")
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def matches_filters(video: VideoCandidate, cfg: SearchConfig) -> bool:
    """Apply the duration / keyword / channel filters from the config."""
    if video.duration is not None:
        if video.duration < cfg.min_duration_sec or video.duration > cfg.max_duration_sec:
            return False
    haystack = f"{video.title} {video.channel or ''}".lower()
    if cfg.require_keywords and not any(k.lower() in haystack for k in cfg.require_keywords):
        return False
    if any(k.lower() in haystack for k in cfg.exclude_keywords):
        return False
    if video.channel_id and video.channel_id in cfg.exclude_channels:
        return False
    if video.channel and video.channel in cfg.exclude_channels:
        return False
    return True


def dedupe(videos: Iterable[VideoCandidate]) -> list[VideoCandidate]:
    seen: set[str] = set()
    out: list[VideoCandidate] = []
    for v in videos:
        if v.video_id in seen:
            continue
        seen.add(v.video_id)
        out.append(v)
    return out


class YouTubeSearcher:
    def __init__(self, cfg: SearchConfig) -> None:
        self.cfg = cfg
        self.backend = self._resolve_backend()

    def _resolve_backend(self) -> str:
        if self.cfg.backend == "auto":
            return "api" if self.cfg.youtube_api_key else "ytdlp"
        if self.cfg.backend == "api" and not self.cfg.youtube_api_key:
            raise SearchError("search.backend='api' requires YOUTUBE_API_KEY")
        return self.cfg.backend

    def search(self) -> list[VideoCandidate]:
        log.info("discovering videos via %s backend", self.backend)
        if self.backend == "api":
            found = self._search_api()
        else:
            found = self._search_ytdlp()
        result = [v for v in dedupe(found) if matches_filters(v, self.cfg)]
        log.info("found %d candidates (%d after filtering)", len(found), len(result))
        return result

    # ---- Data API v3 --------------------------------------------------
    def _api_get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        import requests  # imported lazily so tests don't need the dependency

        params = {**params, "key": self.cfg.youtube_api_key}
        resp = requests.get(f"{API_ROOT}/{endpoint}", params=params, timeout=30)
        if resp.status_code != 200:
            raise SearchError(f"YouTube API {endpoint} failed [{resp.status_code}]: {resp.text}")
        return resp.json()

    def _search_api(self) -> list[VideoCandidate]:
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=self.cfg.published_within_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        ids: list[str] = []
        for query in self.cfg.queries:
            data = self._api_get(
                "search",
                {
                    "part": "id",
                    "q": query,
                    "type": "video",
                    "order": "date",
                    "maxResults": min(self.cfg.max_results, 50),
                    "regionCode": self.cfg.region_code,
                    "relevanceLanguage": self.cfg.relevance_language,
                    "publishedAfter": published_after,
                },
            )
            ids += [
                item["id"]["videoId"]
                for item in data.get("items", [])
                if "videoId" in item["id"]
            ]

        for playlist_id in self.cfg.playlists:
            ids += self._playlist_items(playlist_id)
        for channel_id in self.cfg.channels:
            ids += self._playlist_items(self._uploads_playlist(channel_id))

        return self._hydrate(dedupe_ids(ids))

    def _uploads_playlist(self, channel_id: str) -> str:
        data = self._api_get("channels", {"part": "contentDetails", "id": channel_id})
        items = data.get("items")
        if not items:
            raise SearchError(f"channel not found: {channel_id}")
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def _playlist_items(self, playlist_id: str) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < self.cfg.max_results:
            params: dict[str, Any] = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(self.cfg.max_results - len(ids), 50),
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._api_get("playlistItems", params)
            ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids

    def _hydrate(self, video_ids: list[str]) -> list[VideoCandidate]:
        out: list[VideoCandidate] = []
        for batch_start in range(0, len(video_ids), 50):
            batch = video_ids[batch_start : batch_start + 50]
            data = self._api_get(
                "videos", {"part": "snippet,contentDetails", "id": ",".join(batch)}
            )
            for item in data.get("items", []):
                snippet = item["snippet"]
                try:
                    duration = parse_iso8601_duration(item["contentDetails"]["duration"])
                except ValueError:
                    duration = None
                out.append(
                    VideoCandidate(
                        video_id=item["id"],
                        title=snippet["title"],
                        url=f"https://www.youtube.com/watch?v={item['id']}",
                        channel=snippet.get("channelTitle"),
                        channel_id=snippet.get("channelId"),
                        duration=duration,
                        published_at=snippet.get("publishedAt"),
                    )
                )
        return out

    # ---- yt-dlp -------------------------------------------------------
    def _search_ytdlp(self) -> list[VideoCandidate]:
        targets = [f"ytsearch{self.cfg.max_results}:{q}" for q in self.cfg.queries]
        targets += [f"https://www.youtube.com/playlist?list={p}" for p in self.cfg.playlists]
        targets += [f"https://www.youtube.com/channel/{c}/videos" for c in self.cfg.channels]

        out: list[VideoCandidate] = []
        for target in targets:
            out += self._ytdlp_entries(target)
        return out

    def _ytdlp_entries(self, target: str) -> list[VideoCandidate]:
        import json

        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--playlist-end",
            str(self.cfg.max_results),
            target,
        ]
        log.debug("running: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        except FileNotFoundError as exc:
            raise SearchError("yt-dlp is not installed (pip install yt-dlp)") from exc
        except subprocess.TimeoutExpired as exc:
            raise SearchError(f"yt-dlp timed out on {target}") from exc
        if proc.returncode != 0:
            raise SearchError(f"yt-dlp failed on {target}: {proc.stderr.strip()[:500]}")

        entries: list[VideoCandidate] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            video_id = item.get("id")
            if not video_id:
                continue
            duration = item.get("duration")
            entries.append(
                VideoCandidate(
                    video_id=video_id,
                    title=item.get("title") or video_id,
                    url=item.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                    channel=item.get("channel") or item.get("uploader"),
                    channel_id=item.get("channel_id"),
                    duration=int(duration) if isinstance(duration, (int, float)) else None,
                    published_at=item.get("upload_date"),
                )
            )
        return entries


def dedupe_ids(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out
