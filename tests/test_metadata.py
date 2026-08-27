from yt2tt.config import MetadataConfig
from yt2tt.metadata import build_caption, clean_source_title, normalise_hashtags


def test_clean_strips_brackets_and_hashtags():
    assert clean_source_title("【ENG SUB】Дорама (2024) #shorts") == "Дорама"


def test_clean_collapses_whitespace():
    assert clean_source_title("  Дорама\n\tсерия 1  ") == "Дорама серия 1"


def test_hashtags_are_normalised_and_deduped():
    assert normalise_hashtags(["дорама", "#дорама", " cdrama ", ""]) == ["#дорама", "#cdrama"]


def test_caption_uses_template_and_appends_hashtags():
    cfg = MetadataConfig(title_template="{title} — часть {index}/{total}", hashtags=["#дорама"])
    assert build_caption("Дорама", 2, 5, cfg) == "Дорама — часть 2/5 #дорама"


def test_caption_respects_explicit_hashtag_placeholder():
    cfg = MetadataConfig(title_template="{hashtags} {title} [{index}]", hashtags=["#a", "#b"])
    assert build_caption("T", 1, 3, cfg) == "#a #b T [1]"


def test_caption_is_truncated_but_keeps_hashtags():
    cfg = MetadataConfig(hashtags=["#дорама"], max_title_length=40)
    caption = build_caption("д" * 200, 1, 2, cfg)
    assert len(caption) <= 40
    assert caption.endswith("#дорама")
