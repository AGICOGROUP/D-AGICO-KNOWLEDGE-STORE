"""Invoked by setup.ps1 after the isolated production environment is installed."""

from agico_kb.server_setup import main

if __name__ == "__main__":
    raise SystemExit(main())
