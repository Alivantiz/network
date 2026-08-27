import pytest

from yt2tt.config import Config
from yt2tt.errors import ConfigError


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_valid():
    Config().validate()


def test_yaml_overrides_defaults(tmp_path):
    path = write(tmp_path, "clip:\n  part_seconds: 45\ntiktok:\n  mode: direct\n")
    cfg = Config.load(path, env_file=tmp_path / "missing.env")
    assert cfg.clip.part_seconds == 45
    assert cfg.tiktok.mode == "direct"
    assert cfg.clip.orientation == "blur"  # untouched default


def test_unknown_key_is_rejected(tmp_path):
    path = write(tmp_path, "clip:\n  part_secondz: 45\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        Config.load(path, env_file=tmp_path / "missing.env")


def test_unknown_section_is_rejected(tmp_path):
    path = write(tmp_path, "clipz: {}\n")
    with pytest.raises(ConfigError, match="unknown config section"):
        Config.load(path, env_file=tmp_path / "missing.env")


def test_secrets_come_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setenv("YOUTUBE_API_KEY", "yk")
    cfg = Config.load(None, env_file=tmp_path / "missing.env")
    assert cfg.tiktok.client_key == "ck"
    assert cfg.search.youtube_api_key == "yk"


def test_dotenv_is_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\nTIKTOK_CLIENT_SECRET="cs"\n', encoding="utf-8")
    cfg = Config.load(None, env_file=env)
    assert cfg.tiktok.client_secret == "cs"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda c: setattr(c.clip, "part_seconds", 0), "part_seconds"),
        (lambda c: setattr(c.clip, "orientation", "sideways"), "orientation"),
        (lambda c: setattr(c.tiktok, "chunk_size_mb", 100), "chunk_size_mb"),
        (lambda c: setattr(c.tiktok, "privacy_level", "EVERYONE"), "privacy_level"),
        (lambda c: setattr(c.search, "queries", []), "search needs"),
    ],
)
def test_validation_errors(mutate, message):
    cfg = Config()
    mutate(cfg)
    with pytest.raises(ConfigError, match=message):
        cfg.validate()


def test_missing_tiktok_credentials(monkeypatch):
    for var in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    with pytest.raises(ConfigError, match="TIKTOK_CLIENT_KEY"):
        cfg.require_tiktok_credentials()
