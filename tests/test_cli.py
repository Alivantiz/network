import pytest

from yt2tt.cli import build_parser


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
