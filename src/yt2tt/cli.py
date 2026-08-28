"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
from pathlib import Path

from . import __version__
from .config import Config
from .errors import AuthError, ConfigError, Yt2TtError
from .logging_setup import setup_logging
from .pipeline import Pipeline
from .state import Store
from .tools import find_tool

log = logging.getLogger("yt2tt.cli")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DEFAULT_SCOPES = "user.info.basic,video.upload,video.publish"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt2tt",
        description="Find videos on YouTube, cut them into vertical parts, post them to TikTok.",
    )
    parser.add_argument("--version", action="version", version=f"yt2tt {__version__}")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to the YAML config")
    parser.add_argument("--env-file", default=".env", help="path to the .env file with secrets")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--dry-run", action="store_true", help="do everything except the actual TikTok upload"
    )

    # Repeated on every subcommand so both `yt2tt --dry-run upload` and
    # `yt2tt upload --dry-run` work. SUPPRESS keeps the subcommand copy from
    # overwriting a value already given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", parents=[common], help="check external tools and credentials")
    sub.add_parser("init", parents=[common], help="write a starter config.yaml and .env")
    sub.add_parser("search", parents=[common], help="run discovery only and queue new videos")

    p_add = sub.add_parser("add", parents=[common], help="queue one YouTube URL by hand")
    p_add.add_argument("url", nargs="+")

    p_prepare = sub.add_parser(
        "prepare", parents=[common], help="download queued videos and cut them into parts"
    )
    p_prepare.add_argument("--limit", type=int, default=None, help="how many videos to process")

    p_upload = sub.add_parser("upload", parents=[common], help="post pending parts to TikTok")
    p_upload.add_argument("--limit", type=int, default=None, help="how many clips to post")

    p_run = sub.add_parser("run", parents=[common], help="search + prepare + upload in one go")
    p_run.add_argument("--skip-discovery", action="store_true")
    p_run.add_argument("--upload-limit", type=int, default=None)

    p_status = sub.add_parser("status", parents=[common], help="show queue state")
    p_status.add_argument("--json", action="store_true")

    p_auth = sub.add_parser("auth", parents=[common], help="TikTok OAuth helpers")
    auth_sub = p_auth.add_subparsers(dest="auth_command", required=True)
    p_auth_url = auth_sub.add_parser("url", help="print the authorisation URL to open in a browser")
    p_auth_url.add_argument("--redirect-uri", required=True)
    p_auth_url.add_argument("--scopes", default=DEFAULT_SCOPES)
    p_auth_ex = auth_sub.add_parser("exchange", help="exchange an auth code for tokens")
    p_auth_ex.add_argument("--code", required=True, help="the 'code' query parameter (URL-decoded)")
    p_auth_ex.add_argument("--redirect-uri", required=True)
    auth_sub.add_parser("whoami", help="query creator info with the current token")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    try:
        cfg = Config.load(config_path if config_path.is_file() else None, env_file=args.env_file)
    except Yt2TtError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "dry_run", False):
        cfg.runtime.dry_run = True
    verbose = getattr(args, "verbose", False)
    setup_logging(verbose, Path(cfg.runtime.log_file) if cfg.runtime.log_file else None)

    # Every command runs inside one handler so an expected failure reaches the
    # user as a one-line message instead of a traceback.
    try:
        if args.command == "init":
            return cmd_init()
        if args.command == "auth":
            return cmd_auth(args, cfg)

        cfg.validate()
        with Store(cfg.runtime.state_db) as store:
            pipeline = Pipeline(cfg, store)
            return dispatch(args, cfg, store, pipeline)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except Yt2TtError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130


def dispatch(args: argparse.Namespace, cfg: Config, store: Store, pipeline: Pipeline) -> int:
    if args.command == "doctor":
        return cmd_doctor(cfg)
    if args.command == "search":
        pipeline.discover()
        return 0
    if args.command == "add":
        for url in args.url:
            pipeline.add_url(url)
        return 0
    if args.command == "prepare":
        pipeline.prepare(limit=args.limit)
        return 0
    if args.command == "upload":
        pipeline.upload_pending(limit=args.limit)
        return 0
    if args.command == "run":
        stats = pipeline.run(skip_discovery=args.skip_discovery, upload_limit=args.upload_limit)
        print(
            f"discovered={stats['discovered']} prepared={stats['prepared']} "
            f"uploaded={stats['uploaded']}"
        )
        return 0
    if args.command == "status":
        return cmd_status(store, as_json=args.json)
    parser_error = f"unknown command: {args.command}"
    raise Yt2TtError(parser_error)


def cmd_status(store: Store, *, as_json: bool) -> int:
    summary = store.summary()
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print("videos:")
    for status, count in sorted(summary["videos"].items()):
        print(f"  {status:<12} {count}")
    if not summary["videos"]:
        print("  (empty)")
    print("clips:")
    for status, count in sorted(summary["clips"].items()):
        print(f"  {status:<12} {count}")
    if not summary["clips"]:
        print("  (empty)")
    return 0


def cmd_doctor(cfg: Config) -> int:
    ok = True
    for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
        path = find_tool(tool)
        print(f"[{'ok ' if path else 'MISS'}] {tool}: {path or 'not found in PATH'}")
        ok = ok and bool(path)

    key = cfg.search.youtube_api_key
    key_note = "set" if key else "not set (yt-dlp backend will be used)"
    print(f"[{'ok ' if key else '--'}] YOUTUBE_API_KEY: {key_note}")

    # TikTok credentials only matter for a real upload, so a dry run reports
    # them without failing the check.
    required = not cfg.runtime.dry_run
    for name, env in (
        ("client_key", "TIKTOK_CLIENT_KEY"),
        ("client_secret", "TIKTOK_CLIENT_SECRET"),
        ("refresh_token", "TIKTOK_REFRESH_TOKEN"),
    ):
        value = getattr(cfg.tiktok, name)
        mark = "ok " if value else ("MISS" if required else "--")
        note = "set" if value else ("not set" if required else "not set (not needed for --dry-run)")
        print(f"[{mark}] {env}: {note}")
        if required:
            ok = ok and bool(value)
    print(f"search backend: {'api' if key else 'ytdlp'}   tiktok mode: {cfg.tiktok.mode}")
    return 0 if ok else 1


def cmd_auth(args: argparse.Namespace, cfg: Config) -> int:
    import requests

    if args.auth_command == "url":
        if not cfg.tiktok.client_key:
            print("TIKTOK_CLIENT_KEY is not set", file=sys.stderr)
            return 2
        params = {
            "client_key": cfg.tiktok.client_key,
            "scope": args.scopes,
            "response_type": "code",
            "redirect_uri": args.redirect_uri,
            "state": "yt2tt",
        }
        print(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")
        print("\nOpen this URL, approve, then copy the 'code' parameter from the redirect and run:")
        print(f"  yt2tt auth exchange --code <code> --redirect-uri {args.redirect_uri}")
        return 0

    if args.auth_command == "exchange":
        if not (cfg.tiktok.client_key and cfg.tiktok.client_secret):
            print("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are not set", file=sys.stderr)
            return 2
        try:
            resp = requests.post(
                TOKEN_URL,
                data={
                    "client_key": cfg.tiktok.client_key,
                    "client_secret": cfg.tiktok.client_secret,
                    "code": urllib.parse.unquote(args.code),
                    "grant_type": "authorization_code",
                    "redirect_uri": args.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise AuthError(f"token exchange request failed: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise AuthError(
                f"token exchange returned non-JSON [{resp.status_code}]: {resp.text[:300]}"
            ) from exc
        if "refresh_token" not in payload:
            print(f"exchange failed: {payload}", file=sys.stderr)
            return 1
        print("Add these to your .env:\n")
        print(f"TIKTOK_ACCESS_TOKEN={payload['access_token']}")
        print(f"TIKTOK_REFRESH_TOKEN={payload['refresh_token']}")
        print(f"\n(access token expires in {payload.get('expires_in')}s;")
        print("the refresh token is reused and rotated automatically)")
        return 0

    if args.auth_command == "whoami":
        from .uploaders.tiktok import TikTokUploader

        cfg.require_tiktok_credentials()
        uploader = TikTokUploader(
            cfg.tiktok, token_cache=Path(cfg.runtime.state_db).with_name(".tiktok_token.json")
        )
        print(json.dumps(uploader.creator_info(), ensure_ascii=False, indent=2))
        return 0

    return 2


def cmd_init() -> int:
    """Copy the packaged starter files into the working directory.

    The templates ship inside the package rather than being read from the
    source tree, so ``init`` works the same from a wheel and from a checkout.
    """
    from importlib import resources

    templates = resources.files("yt2tt.templates")
    pairs = [("config.example.yaml", Path("config.yaml")), ("env.example", Path(".env"))]
    failed = False
    for name, dst in pairs:
        if dst.exists():
            print(f"skip {dst} (already exists)")
            continue
        try:
            dst.write_text((templates / name).read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            print(f"could not create {dst} from template {name}: {exc}", file=sys.stderr)
            failed = True
            continue
        print(f"created {dst}")
    if failed:
        return 1
    print("\nEdit config.yaml (queries, part length) and .env (keys), then run: yt2tt doctor")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
