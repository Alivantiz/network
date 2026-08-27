"""Building TikTok captions for the individual parts."""

from __future__ import annotations

import re

from .config import MetadataConfig

_WS = re.compile(r"\s+")
_HASHTAG = re.compile(r"#\S+")
_BRACKETS = re.compile(r"[\[\(【][^\]\)】]*[\]\)】]")


def clean_source_title(title: str, *, strip_brackets: bool = True) -> str:
    """Normalise a YouTube title so it reads well as a caption prefix."""
    text = title or ""
    if strip_brackets:
        text = _BRACKETS.sub(" ", text)
    text = _HASHTAG.sub(" ", text)
    text = _WS.sub(" ", text).strip(" -–—|·•")
    return text.strip()


def normalise_hashtags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        tag = _WS.sub("", tag)
        if tag not in out:
            out.append(tag)
    return out


def build_caption(
    source_title: str,
    index: int,
    total: int,
    cfg: MetadataConfig,
    *,
    channel: str | None = None,
) -> str:
    """Render the caption for one part, truncating to TikTok's limit."""
    hashtags = normalise_hashtags(cfg.hashtags)
    tag_text = " ".join(hashtags)
    base = cfg.title_template.format(
        title=clean_source_title(source_title),
        index=index,
        total=total,
        channel=channel or "",
        hashtags=tag_text,
    )
    base = _WS.sub(" ", base).strip()

    if "{hashtags}" not in cfg.title_template and tag_text:
        limit = max(0, cfg.max_title_length - len(tag_text) - 1)
        if len(base) > limit:
            base = base[: max(0, limit - 1)].rstrip() + "…"
        caption = f"{base} {tag_text}".strip()
    else:
        caption = base

    if len(caption) > cfg.max_title_length:
        caption = caption[: cfg.max_title_length - 1].rstrip() + "…"
    return caption
