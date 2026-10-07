"""Non-destructive Step 4 system verification for the PRIME bot dashboard.

This script intentionally does not import main.py or start Discord. It parses
the lifecycle entry point statically, imports the HTTP route module only, and
opens SQLite in read-only mode.
"""

from __future__ import annotations

import ast
import importlib
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = ROOT / "bot_database.db"

STATIC_FILES = (
    ROOT / "main.py",
    ROOT / "database.py",
    ROOT / "web_server.py",
    ROOT / "cogs" / "community.py",
    ROOT / "dashboard" / "app.js",
    ROOT / "dashboard" / "app.css",
    ROOT / "dashboard" / "index.html",
)
PYTHON_SOURCE_FILES = tuple(sorted((ROOT / "cogs").glob("*.py")))

EXPECTED_TABLES = (
    "clan_applications",
    "clan_rosters",
    "scrim_logs",
    "ticket_dropdown_configs",
    "ticket_dropdown_categories",
    "broadcast_logs",
)

REQUIRED_GUILD_ROUTES = (
    "/api/guilds/{guild_id}/clan/applications",
    "/api/guilds/{guild_id}/clan/applications/{application_id}/action",
    "/api/guilds/{guild_id}/clan/roster",
    "/api/guilds/{guild_id}/clan/roster/publish",
    "/api/guilds/{guild_id}/clan/scrims",
    "/api/guilds/{guild_id}/tickets/dropdown-config",
    "/api/guilds/{guild_id}/tickets/dropdown-config/publish",
    "/api/guilds/{guild_id}/broadcast/history",
    "/api/guilds/{guild_id}/broadcast/send",
    "/api/guilds/{guild_id}/security/lockdown",
)


class VerificationError(RuntimeError):
    """A required system invariant is not satisfied."""


def parse_sources() -> dict[Path, ast.AST]:
    trees: dict[Path, ast.AST] = {}
    for path in (*STATIC_FILES, *PYTHON_SOURCE_FILES):
        if not path.is_file():
            raise VerificationError(f"missing static asset: {path.relative_to(ROOT)}")
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            try:
                trees[path] = ast.parse(source, filename=str(path))
                compile(source, str(path), "exec")
            except SyntaxError as error:
                raise VerificationError(
                    f"syntax error in {path.relative_to(ROOT)}: {error}"
                ) from error
    return trees


def verify_database() -> str:
    if not DATABASE_PATH.is_file():
        raise VerificationError("bot_database.db does not exist")
    uri = f"file:{DATABASE_PATH}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise VerificationError(f"SQLite integrity check returned {integrity!r}")
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    missing = [table for table in EXPECTED_TABLES if table not in existing]
    if missing:
        raise VerificationError(f"missing database tables: {', '.join(missing)}")

    source = (ROOT / "database.py").read_text(encoding="utf-8")
    for table in EXPECTED_TABLES:
        pattern = rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{re.escape(table)}\b"
        if not re.search(pattern, source, flags=re.IGNORECASE):
            raise VerificationError(
                f"{table} is not guarded by CREATE TABLE IF NOT EXISTS"
            )
    return f"OK ({len(EXPECTED_TABLES)} additive tables, integrity_check=ok)"


def verify_routes() -> str:
    try:
        web_server = importlib.import_module("web_server")
    except Exception as error:
        raise VerificationError(f"web_server import failed: {error}") from error

    route_defs = getattr(getattr(web_server, "routes", None), "_items", ())
    route_paths = {getattr(route, "path", "") for route in route_defs}
    missing = [path for path in REQUIRED_GUILD_ROUTES if path not in route_paths]
    if missing:
        raise VerificationError(f"missing API routes: {', '.join(missing)}")
    if not hasattr(web_server, "authorize"):
        raise VerificationError("shared authorize middleware is missing")
    legacy_routes = {
        "/api/guild/{guild_id}/tickets/dropdown-config",
        "/api/guild/{guild_id}/broadcast/send",
        "/api/guild/{guild_id}/security/lockdown",
    }
    missing_legacy = sorted(legacy_routes - route_paths)
    if missing_legacy:
        raise VerificationError(f"missing compatibility routes: {', '.join(missing_legacy)}")
    return f"OK ({len(route_defs)} registered routes, auth middleware present)"


def verify_main(trees: dict[Path, ast.AST]) -> str:
    tree = trees[ROOT / "main.py"]
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    required_functions = {"setup_hook", "close"}
    missing = required_functions - function_names
    if missing:
        raise VerificationError(f"main.py lifecycle methods missing: {sorted(missing)}")
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for required_call in ("await init_db()", "await start_web_server(self)"):
        if required_call not in source:
            raise VerificationError(f"main.py lifecycle call missing: {required_call}")
    return "OK (main.py parses; init_db and web server lifecycle are present)"


def verify_views() -> str:
    try:
        community = importlib.import_module("cogs.community")
        view_class = community.PersistentDropdownTicketView
        view = view_class(
            [{"label": "Verification", "description": "System check", "emoji": "✅"}],
            guild_id=1,
        )
    except Exception as error:
        raise VerificationError(f"community/view import failed: {error}") from error
    if view.timeout is not None:
        raise VerificationError("PersistentDropdownTicketView timeout is not None")
    select_ids = {
        getattr(child, "custom_id", None)
        for child in getattr(view, "children", ())
    }
    if view_class.CUSTOM_ID not in select_ids or view_class.CUSTOM_ID != "ticket_dropdown_select":
        raise VerificationError("ticket dropdown custom_id contract is broken")
    source = (ROOT / "cogs" / "community.py").read_text(encoding="utf-8")
    if "bot.add_view(" not in source or "message_id=int(config[\"message_id\"])" not in source:
        raise VerificationError("restart-time persistent view registration is missing")
    return "OK (timeout=None, static custom_id, restart registration present)"


def verify_static_assets(trees: dict[Path, ast.AST]) -> str:
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "dashboard" / "app.css").read_text(encoding="utf-8")
    for marker in ('id="app"', 'static/app.css', 'static/app.js'):
        if marker not in html:
            raise VerificationError(f"dashboard/index.html missing {marker}")
    for marker in ("--bg-pitch", ".bento-container", ".bottom-floating-nav"):
        if marker not in css:
            raise VerificationError(f"dashboard/app.css missing AMOLED marker {marker}")
    for marker in ("function renderPage", "function securityView", "broadcastView"):
        if marker not in js:
            raise VerificationError(f"dashboard/app.js missing handler {marker}")

    slash_decorators = 0
    for tree in trees.values():
        if not isinstance(tree, ast.AST):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                text = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                if "app_commands.command" in text:
                    slash_decorators += 1
    if slash_decorators < 82:
        raise VerificationError(
            f"expected at least 82 Slash command definitions, found {slash_decorators}"
        )
    return f"OK (HTML/CSS/JS assets intact; {slash_decorators} Slash definitions discovered)"


def main() -> int:
    try:
        trees = parse_sources()
        print("[✔] Database Tables: " + verify_database())
        print("[✔] Web Routes & Auth Middleware: " + verify_routes())
        print("[✔] Discord Views & Handlers: " + verify_views())
        print("[✔] Static Assets (CSS/JS/HTML): " + verify_static_assets(trees))
        print("[✔] Main Lifecycle: " + verify_main(trees))
        print("SYSTEM VERIFICATION: PASS")
        return 0
    except VerificationError as error:
        print(f"[✘] SYSTEM VERIFICATION: FAIL — {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())