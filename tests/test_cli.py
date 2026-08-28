import pytest

from yt2tt.cli import build_parser, cmd_doctor, cmd_init, main
from yt2tt.config import Config


def flag(argv, name):
    return getattr(build_parser().parse_args(argv), name, False)


@pytest.mark.parametrize(
    "argv",
    [["--dry-run", "upload"], ["upload", "--dry-run"], ["--dry-run", "run"], ["run", "--dry-run"]],
)
def test_dry_run_accepted_on_both_sides_of_the_subcommand(argv):
    assert flag(argv, "dry_run") is True


@pytest.mark.parametrize("argv", [["-v", "prepare"], ["prepare", "-v"]])
def test_verbose_accepted_on_both_sides(argv):
    assert flag(argv, "verbose") is True


def test_flags_default_to_off():
    args = build_parser().parse_args(["status"])
    assert getattr(args, "dry_run", False) is False
    assert getattr(args, "verbose", False) is False


def test_subcommand_options_still_parse():
    args = build_parser().parse_args(["--dry-run", "upload", "--limit", "3"])
    assert (args.command, args.limit, args.dry_run) == ("upload", 3, True)


def test_add_takes_several_urls():
    args = build_parser().parse_args(["add", "https://youtu.be/a", "https://youtu.be/b"])
    assert len(args.url) == 2


def test_auth_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["auth"])


def test_init_writes_the_packaged_templates(tmp_path, monkeypatch, capsys):
    """init must work from an installed wheel, not only from a source checkout."""
    monkeypatch.chdir(tmp_path)
    assert cmd_init() == 0

    config = tmp_path / "config.yaml"
    env = tmp_path / ".env"
    assert "search:" in config.read_text(encoding="utf-8")
    assert "TIKTOK_CLIENT_KEY" in env.read_text(encoding="utf-8")
    assert "created config.yaml" in capsys.readouterr().out


def test_init_leaves_existing_files_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("mine\n", encoding="utf-8")
    assert cmd_init() == 0
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == "mine\n"
    assert "skip config.yaml" in capsys.readouterr().out


def test_doctor_needs_tiktok_keys_for_a_real_run(monkeypatch):
    monkeypatch.setattr("yt2tt.cli.find_tool", lambda name: f"/usr/bin/{name}")
    assert cmd_doctor(Config()) == 1


def test_doctor_passes_without_tiktok_keys_in_a_dry_run(monkeypatch, capsys):
    monkeypatch.setattr("yt2tt.cli.find_tool", lambda name: f"/usr/bin/{name}")
    cfg = Config()
    cfg.runtime.dry_run = True
    assert cmd_doctor(cfg) == 0
    assert "not needed for --dry-run" in capsys.readouterr().out


def test_doctor_fails_when_a_tool_is_missing(monkeypatch):
    monkeypatch.setattr("yt2tt.cli.find_tool", lambda name: None)
    cfg = Config()
    cfg.runtime.dry_run = True
    assert cmd_doctor(cfg) == 1


def test_auth_without_credentials_explains_instead_of_crashing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    assert main(["auth", "whoami"]) == 2
    assert "config error: missing TikTok credentials" in capsys.readouterr().err
