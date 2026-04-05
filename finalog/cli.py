"""CLI entry point for finalog."""

import argparse
import sys


def cmd_config(_args):
    """Run interactive configuration setup."""
    from finalog.config import run_setup
    run_setup()


def cmd_start(_args):
    """Launch the screen-capture app."""
    from finalog import config

    if not config.is_configured():
        print("finalog is not configured yet. Running setup first...\n")
        config.run_setup()
        if not config.is_configured():
            print("Setup incomplete — exiting.")
            sys.exit(1)
        print()

    # Push saved config into env vars so the rest of the code picks them up
    config.apply_to_env()

    # Now import and run the app (heavy imports deferred to here)
    from finalog.app import run
    run()


def main():
    parser = argparse.ArgumentParser(
        prog="finalog",
        description="Capture banking transactions from screen and log to Google Sheets",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("config", help="Configure API keys and credentials")
    sub.add_parser("start", help="Launch the screen-capture app")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    {"config": cmd_config, "start": cmd_start}[args.command](args)


if __name__ == "__main__":
    main()
