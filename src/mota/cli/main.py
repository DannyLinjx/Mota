"""Mota CLI: native desktop launcher plus full headless command surface."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import typer

from mota import __version__
from mota.app.service import ApplicationService
from mota.app.state import SessionContext
from mota.errors import MotaError
from mota.workspace import paths
from mota.workspace.atomic import read_json, write_json_atomic

app = typer.Typer(add_completion=False, no_args_is_help=False,
                  help="Mota - agentic PCB workspace (native desktop + headless engine)")
auth_app = typer.Typer(help="Local account commands")
config_app = typer.Typer(help="Model and preference configuration")
model_app = typer.Typer(help="Model provider settings")
key_app = typer.Typer(help="API key storage")
catalog_app = typer.Typer(help="Catalog snapshots")
library_app = typer.Typer(help="Versioned KiCad library import")
templates_app = typer.Typer(help="Circuit templates")
project_app = typer.Typer(help="Projects and revisions")
design_app = typer.Typer(help="Typed design edits")
run_app = typer.Typer(help="Build runs")
memory_app = typer.Typer(help="Markdown memory")
benchmark_app = typer.Typer(help="Engine benchmarks")
app.add_typer(auth_app, name="auth")
app.add_typer(config_app, name="config")
config_app.add_typer(model_app, name="model")
config_app.add_typer(key_app, name="api-key")
app.add_typer(catalog_app, name="catalog")
app.add_typer(library_app, name="library")
app.add_typer(templates_app, name="templates")
app.add_typer(project_app, name="project")
app.add_typer(design_app, name="design")
app.add_typer(run_app, name="run")
app.add_typer(memory_app, name="memory")
app.add_typer(benchmark_app, name="benchmark")

EXIT_OK = 0
EXIT_INPUT_REQUIRED = 2
EXIT_PARTIAL = 3
EXIT_UNAVAILABLE = 4
EXIT_INPUT_ERROR = 5
EXIT_FAILED = 6
EXIT_CANCELLED = 130


def _out(payload: Any, as_json: bool = False, plain: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif plain is not None:
        typer.echo(plain)
    else:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _fail(exc: MotaError, as_json: bool) -> int:
    if as_json:
        typer.echo(json.dumps({"error": exc.to_error()}, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"Error [{exc.code}]: {exc.message}", err=True)
    code = exc.code
    if code in ("MODEL_UNAVAILABLE", "MODEL_AUTH_FAILED", "CONFIG_CONFLICT"):
        return EXIT_UNAVAILABLE
    if code in ("AUTH_REQUIRED", "AUTH_FAILED", "ACCESS_DENIED"):
        return EXIT_UNAVAILABLE
    if code in ("REVISION_CONFLICT", "DRAFT_CONFLICT", "CONSTRAINT_CONFLICT",
                "MISSING_REQUIRED_FIELD", "INVALID_REQUEST", "PART_NOT_FOUND",
                "PINMAP_MISMATCH", "DUPLICATE_REFERENCE", "COMPATIBILITY_UNVERIFIED"):
        return EXIT_INPUT_ERROR
    if code in ("PROJECT_BUSY",):
        return EXIT_UNAVAILABLE
    return EXIT_FAILED


def _service() -> ApplicationService:
    return ApplicationService()


def _session_path() -> Path:
    return paths.ensure_dir(paths.app_dir()) / "cli_session.json"


def _token_path() -> Path:
    return paths.ensure_dir(paths.app_dir()) / "cli_tokens.json"


def _save_cli_session(ctx: SessionContext, token: str) -> None:
    digest = hashlib.sha256(token.encode()).hexdigest()
    tokens = read_json(_token_path(), default={"schema_version": 1, "tokens": {}}) or {}
    tokens.setdefault("tokens", {})[digest] = {"user_id": ctx.user_id,
                                              "username": ctx.username}
    write_json_atomic(_token_path(), tokens)
    write_json_atomic(_session_path(), {"schema_version": 1, "user_id": ctx.user_id,
                                        "username": ctx.username, "token": token,
                                        "session_id": ctx.session_id})


def _load_session() -> SessionContext | None:
    data = read_json(_session_path(), default=None)
    if not data:
        return None
    token = data.get("token", "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    tokens = read_json(_token_path(), default={"tokens": {}}) or {}
    entry = tokens.get("tokens", {}).get(digest)
    if not entry or entry.get("user_id") != data.get("user_id"):
        return None
    return SessionContext(session_id=data.get("session_id") or "session_cli",
                          user_id=data["user_id"], username=entry["username"], epoch=1)


def _require_session() -> tuple[ApplicationService, SessionContext]:
    session = _load_session()
    if session is None:
        raise MotaError("Sign in first: mota auth login", code="AUTH_REQUIRED")
    return _service(), session


def _new_session(payload: dict[str, Any]) -> tuple[SessionContext, str]:
    info = payload["session"]
    ctx = SessionContext(session_id=info["session_id"], user_id=info["user_id"],
                         username=info["username"], epoch=info.get("epoch", 1))
    token = secrets.token_urlsafe(32)
    _save_cli_session(ctx, token)
    return ctx, token


# --------------------------------------------------------------------------- root
@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _launch_desktop()


@app.command()
def desktop() -> None:
    """Open the native Mota desktop window."""
    _launch_desktop()


def _launch_desktop() -> None:
    try:
        from mota.desktop.main import run_desktop
    except ImportError as exc:
        typer.echo(f"Desktop UI is unavailable: {exc}", err=True)
        raise typer.Exit(EXIT_UNAVAILABLE)
    raise typer.Exit(run_desktop())


@app.command()
def engine(stdio: bool = typer.Option(False, "--stdio", help="Serve JSON-RPC on stdio")) -> None:
    """Run the engine process used by the desktop app."""
    if not stdio:
        typer.echo("Only --stdio is supported for the engine process.", err=True)
        raise typer.Exit(EXIT_INPUT_ERROR)
    from mota.protocol.stdio import run_stdio_engine

    raise typer.Exit(run_stdio_engine())


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json"),
           probe_model: bool = typer.Option(False, "--probe-model")) -> None:
    """Report runtime, data directory, catalog and model readiness."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, next_step: str = "") -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL",
                       "detail": detail, "next_step": next_step})

    add("python", sys.version_info >= (3, 11), sys.version.split()[0],
        "Install Python 3.11 or newer")
    try:
        import PySide6

        add("qt", True, f"PySide6 {PySide6.__version__}")
    except Exception as exc:  # noqa: BLE001
        add("qt", False, f"PySide6 unavailable: {exc}", "pip install PySide6")
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401

        os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", ""))
        add("qt_widgets", True, "Qt Widgets importable")
    except Exception as exc:  # noqa: BLE001
        add("qt_widgets", False, str(exc), "Reinstall PySide6")
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget  # noqa: F401

        add("opengl_widget", True, "QOpenGLWidget available; actual GL context is probed "
                                   "when the 3D view opens")
    except Exception as exc:  # noqa: BLE001
        add("opengl_widget", False, str(exc), "Use PCB 2D; install a working OpenGL driver")
    root = paths.data_root()
    try:
        paths.ensure_dir(root)
        probe = root / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("data_dir", True, str(root))
    except Exception as exc:  # noqa: BLE001
        add("data_dir", False, f"{root}: {exc}", "Choose a writable MOTA_DATA_DIR")
    session = _load_session()
    if session:
        try:
            from mota.app.config import ConfigService
            from mota.auth.credentials import SecretStore

            cfg = ConfigService(session.scope(), SecretStore()).get()
            add("account", True, session.username)
            add("model_api_key", bool(cfg.api_key_configured),
                "configured" if cfg.api_key_configured else "not configured",
                "mota config api-key set")
            add("model_verified", cfg.model_verified,
                f"verified at config v{cfg.model_verified_config_version}"
                if cfg.model_verified else f"not probed (model {cfg.model})",
                "mota config probe-model")
        except Exception as exc:  # noqa: BLE001
            add("account", False, str(exc), "mota auth login")
    else:
        add("account", True, "no CLI session (run `mota auth login`)")
        add("model_api_key", False, "not configured for this CLI session",
            "mota auth login && mota config api-key set")
    from mota.catalog import builtin as catalog_builtin

    add("catalog", True,
        f"builtin curated snapshot ({len(catalog_builtin.PARTS)} parts, "
        f"{len(catalog_builtin.FOOTPRINTS)} footprints, 3 recipe-ready templates)")
    import shutil as _shutil

    kicad = _shutil.which("kicad-cli")
    add("kicad_cli", bool(kicad),
        kicad or "kicad-cli not found (optional; KiCad export stays disabled)",
        "Install KiCad 10 and run the external compatibility test")
    if probe_model:
        typer.echo("--probe-model requires an authenticated session and performs one paid "
                   "API request. Use `mota config probe-model`.", err=True)
    result = {"ok": all(c["status"] == "PASS" for c in checks if c["check"] in
                        ("python", "qt", "data_dir")),
              "version": __version__, "checks": checks,
              "data_root": str(root)}
    _out(result, json_output,
         "\n".join(f"{c['status']:4} {c['check']:16} {c['detail']}" for c in checks))
    raise typer.Exit(EXIT_OK if result["ok"] else EXIT_UNAVAILABLE)


# --------------------------------------------------------------------------- auth
@auth_app.command("register")
def auth_register(username: str = typer.Option(..., prompt=True),
                  display_name: str = typer.Option(""),
                  password: str = typer.Option(..., prompt=True, hide_input=True),
                  confirm: str = typer.Option(..., prompt="Confirm password", hide_input=True),
                  json_output: bool = typer.Option(False, "--json")) -> None:
    if password != confirm:
        typer.echo("Passwords do not match.", err=True)
        raise typer.Exit(EXIT_INPUT_ERROR)
    try:
        service = _service()
        result = service.dispatch("auth.register", {
            "username": username, "password": password,
            "display_name": display_name or None}, None)
        ctx, _ = _new_session(result)
        _out({"profile": result["profile"], "session": {"user_id": ctx.user_id,
                                                         "username": ctx.username}},
             json_output, f"Created local account {ctx.username}.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@auth_app.command("login")
def auth_login(username: str = typer.Option(..., prompt=True),
               password: str = typer.Option(..., prompt=True, hide_input=True),
               remember: bool = typer.Option(False, "--remember"),
               json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service = _service()
        result = service.dispatch("auth.login", {
            "username": username, "password": password, "remember_me": remember}, None)
        ctx, _ = _new_session(result)
        _out({"profile": result["profile"], "session": {"user_id": ctx.user_id,
                                                         "username": ctx.username}},
             json_output, f"Signed in as {ctx.username}.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@auth_app.command("logout")
def auth_logout(json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("auth.logout", {}, session)
        if _session_path().exists():
            _session_path().unlink()
        _out(result, json_output, "Signed out.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@auth_app.command("status")
def auth_status(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _load_session()
    payload = {"authenticated": bool(session),
               "profile": {"username": session.username, "user_id": session.user_id}
               if session else None}
    _out(payload, json_output,
         f"Signed in as {session.username}" if session else "Not signed in.")


# ------------------------------------------------------------------------- config
@model_app.command("set")
def config_model_set(provider: str = typer.Option("openai"),
                     model: str = typer.Option(..., prompt=True),
                     base_url: str = typer.Option(
                         "", help="Defaults to the provider preset"),
                     api_format: str = typer.Option(
                         "", help="responses or chat_completions (defaults to the "
                                  "selected provider preset)"),
                     profile: str = typer.Option("", help="AI connection profile id"),
                     json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        from mota.app.config import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["openai"])
        service, session = _require_session()
        current = service.dispatch("config.get", {}, session)["config"]
        profile_id = profile or current.get("default_profile_id") or "openai-main"
        entry: dict[str, Any] = {
            "profile_id": profile_id, "provider": provider, "model": model,
            "base_url": base_url or preset["base_url"],
            "api_format": api_format or preset["api_format"]}
        result = service.dispatch("config.update", {
            "expected_config_version": current["config_version"],
            "changes": {"profiles": [entry]},
            "idempotency_key": "config_" + secrets.token_hex(4)}, session)
        saved = next((p for p in result["config"]["profiles"]
                      if p["profile_id"] == profile_id), {})
        _out(result, json_output,
             f"Model set to {saved.get('model', model)} for {profile_id} "
             f"({saved.get('provider', provider)} · "
             f"{saved.get('api_format', '')}, config "
             f"v{result['config']['config_version']}).")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@key_app.command("set")
def config_api_key_set(session_only: bool = typer.Option(False, "--session-only"),
                       profile: str = typer.Option("", help="AI connection profile id"),
                       json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        key = typer.prompt("API key", hide_input=True)
        current = service.dispatch("config.get", {}, session)["config"]
        profile_id = profile or current.get("default_profile_id")
        result = service.dispatch("config.update", {
            "expected_config_version": current["config_version"],
            "changes": {"api_key_action": "set", "api_key": key,
                        "api_key_profile_id": profile_id,
                        "api_key_session_only": session_only},
            "idempotency_key": "config_" + secrets.token_hex(4)}, session)
        _out({"api_key_configured": result["config"]["api_key_configured"],
              "secret_storage": result["config"]["secret_storage"]}, json_output,
             f"API key stored for {profile_id} "
             f"({result['config']['secret_storage']}).")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@config_app.command("use")
def config_use(profile_id: str = typer.Argument(..., help="AI connection profile id"),
               json_output: bool = typer.Option(False, "--json")) -> None:
    """Set the default connection for new runs (existing runs keep their binding)."""
    try:
        service, session = _require_session()
        current = service.dispatch("config.get", {}, session)["config"]
        result = service.dispatch("config.update", {
            "expected_config_version": current["config_version"],
            "changes": {"set_default_profile_id": profile_id},
            "idempotency_key": "config_" + secrets.token_hex(4)}, session)
        _out(result, json_output,
             f"Default connection is now {result['default_profile_id']}. "
             f"Existing runs keep their frozen connection.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@config_app.command("show")
def config_show(json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("config.get", {}, session)
        cfg = result["config"]
        default_id = cfg.get("default_profile_id")
        names = {p["profile_id"]: p.get("name") or p["profile_id"]
                 for p in cfg.get("profiles", [])}
        _out(cfg, json_output,
             f"{len(cfg.get('profiles', []))} connection(s) · default "
             f"{names.get(default_id, default_id)} · model {cfg['model']} · "
             f"key {'configured' if cfg['api_key_configured'] else 'missing'} · "
             f"config v{cfg['config_version']}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@config_app.command("probe-model")
def config_probe(suite: list[str] = typer.Option(
                     None, "--suite", help="text, tool_roundtrip or vision"),
                 profile: str = typer.Option("", help="AI connection profile id"),
                 json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        cfg = service.dispatch("config.get", {}, session)["config"]
        started = service.dispatch("config.probe_model", {
            "expected_config_version": cfg["config_version"],
            "profile_id": profile or None,
            "test_suite": suite or ["text"]}, session)
        task_id = started["task_id"]
        result = _poll_task(service, session, task_id, json_output)
        suites = result.get("suites") or []
        summary = ", ".join(f"{row.get('suite')} {row.get('status')}" for row in suites)
        _out(result, json_output,
             f"Probe {'succeeded' if result.get('ok') else 'failed'} · {summary}")
        raise typer.Exit(EXIT_OK if result.get("ok") else EXIT_UNAVAILABLE)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


def _poll_task(service: ApplicationService, session: SessionContext, task_id: str,
               quiet: bool = False) -> dict[str, Any]:
    last_seq = 0
    while True:
        data = service.dispatch("task.inspect", {"task_id": task_id,
                                                 "after_seq": last_seq}, session)
        for event in data["events"]:
            last_seq = max(last_seq, event.get("seq", 0))
            if not quiet and event["type"] == "task.progress":
                payload = event["payload"]
                typer.echo(f"  {payload.get('stage', '')}: "
                           f"{payload.get('completed', '')}/{payload.get('total', '')} "
                           f"{payload.get('message', '')}".rstrip())
            if event["type"] == "task.failed":
                error = event["payload"].get("error", {})
                raise MotaError(error.get("message", "Task failed"),
                                code=error.get("code", "TASK_FAILED"))
        task = data["task"]
        if task["state"] in ("SUCCEEDED",):
            return task.get("result", {})
        if task["state"] in ("FAILED", "CANCELLED"):
            error = task.get("error") or {}
            raise MotaError(error.get("message", "Task failed"),
                            code=error.get("code", "TASK_FAILED"))
        time.sleep(0.4)


# ------------------------------------------------------------------------ catalog
@catalog_app.command("sync")
def catalog_sync(source: str = typer.Option("kicad"),
                 library_dir: str = typer.Option(""),
                 release_ref: str = typer.Option("master"),
                 json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        started = service.dispatch("catalog.sync", {
            "source": source, "library_dir": library_dir or None,
            "release_ref": release_ref}, session)
        result = _poll_task(service, session, started["task_id"], json_output)
        _out(result, json_output, "Catalog snapshot ready: "
             + json.dumps(result.get("coverage", {}), ensure_ascii=False))
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))
    except Exception as exc:  # noqa: BLE001 - network/archive errors
        typer.echo(f"Catalog sync failed: {exc}", err=True)
        raise typer.Exit(EXIT_FAILED)


@catalog_app.command("status")
def catalog_status_cmd(json_output: bool = typer.Option(False, "--json")) -> None:
    service, session = _require_session()
    result = service.dispatch("catalog.status", {}, session)
    builtin = result.get("builtin") or {}
    _out(result, json_output,
         f"Active snapshot: {result.get('active_snapshot_id')} · "
         f"builtin parts {builtin.get('parts', '?')} · "
         f"footprints {builtin.get('footprints', '?')} · "
         f"recipe-ready templates {builtin.get('recipe_ready', 3)}")


@library_app.command("import")
def library_import(source: Path = typer.Option(..., "--source", help="File or directory"),
                   source_id: str = typer.Option(..., "--source-id"),
                   version: str = typer.Option("unknown", "--version"),
                   license_name: str = typer.Option("unknown", "--license"),
                   license_url: str = typer.Option("", "--license-url"),
                   format: str = typer.Option("auto", "--format"),
                   library_name: str = typer.Option("", "--library-name"),
                   reference_only: bool = typer.Option(
                       False, "--reference-only",
                       help="Record hashes only; do not copy source bytes into the snapshot"),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        report = service.dispatch("library.import", {
            "source_id": source_id, "path": str(source.expanduser()),
            "version": version, "license": license_name,
            "license_url": license_url, "format": format,
            "library_name": library_name or None,
            "store_content": not reference_only}, session)
        counts = report["counts"]
        _out(report, json_output,
             f"Library {source_id} {report['status']} · snapshot {report['snapshot_id']} · "
             f"{counts['discovered']} assets "
             f"(parsed {counts['parsed']}, unsupported {counts['unsupported']}, "
             f"failed {counts['failed']}) · {report['duration_ms']} ms")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@library_app.command("status")
def library_status_cmd(json_output: bool = typer.Option(False, "--json")) -> None:
    result = _service().dispatch("library.status", {}, None)
    if json_output:
        _out(result, True)
        return
    if not result["sources"]:
        typer.echo("No imported library snapshots.")
        return
    for row in result["sources"]:
        counts = row.get("counts", {})
        typer.echo(f"{row['source_id']:24} {row['snapshot_id']:18} "
                   f"{counts.get('indexed', 0)} assets · "
                   f"parsed {counts.get('parsed', 0)} · "
                   f"unsupported {counts.get('unsupported', 0)} · "
                   f"failed {counts.get('failed', 0)}")


@library_app.command("search")
def library_search(query: str = typer.Argument(""),
                   source_id: str = typer.Option("", "--source-id"),
                   kind: str = typer.Option("", "--kind"),
                   status: str = typer.Option("", "--status"),
                   limit: int = typer.Option(20, "--limit"),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    result = _service().dispatch("library.search", {
        "query": query, "source_id": source_id or None,
        "kind": kind or None, "status": status or None, "limit": limit}, None)
    if json_output:
        _out(result, True)
        return
    for asset in result["candidates"]:
        typer.echo(f"{asset['asset_id']:48} {asset['status']:12} "
                   f"{asset['pin_count']:>3}p {asset['pad_count']:>3}f  "
                   f"{asset['description'][:40]}")
    typer.echo(f"{result['total']} match(es)")


@library_app.command("inspect")
def library_inspect_cmd(asset_id: str = typer.Option(..., "--asset-id"),
                        json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        result = _service().dispatch("library.inspect", {"asset_id": asset_id}, None)
        _out(result, json_output,
             f"{result['asset_id']} · {result['status']} · "
             f"{len(result['pins'])} pins · {len(result['pads'])} pads · "
             f"units {result['units']} · styles {result['body_styles']}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@library_app.command("preview")
def library_preview_cmd(asset_id: str = typer.Option(..., "--asset-id"),
                        out: Path | None = typer.Option(None, "--out"),
                        json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        result = _service().dispatch("library.preview", {"asset_id": asset_id}, None)
        if out is not None:
            target = out.expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result["svg"], encoding="utf-8")
            _out({"asset_id": asset_id, "path": str(target)}, json_output,
                 f"Wrote {target}")
        else:
            _out(result, json_output, result["svg"])
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@library_app.command("rollback")
def library_rollback_cmd(source_id: str = typer.Option(..., "--source-id"),
                          json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("library.rollback", {"source_id": source_id}, session)
        _out(result, json_output,
             f"Rolled {source_id} back from {result['replaced_snapshot_id']} "
             f"to {result['snapshot_id']}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


# --------------------------------------------------------------------------
# Component library (versioned packages, not the legacy snapshot importer)
# --------------------------------------------------------------------------

@library_app.command("list")
def component_library_list(kind: str = typer.Option("", "--kind",
                                                    help="component|symbol|footprint"),
                           namespace: str = typer.Option("", "--namespace"),
                           query: str = typer.Option("", "--query"),
                           limit: int = typer.Option(40, "--limit"),
                           json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library import resolver

    result = resolver.search(query, kind=kind or None,
                             namespace=namespace or None, limit=limit)
    if json_output:
        _out(result, True)
        return
    for row in result["candidates"]:
        typer.echo(f"{row['asset_id']:56} {row['kind']:9} "
                   f"{row['package_version']:<11} {row['documentation_status']:8} "
                   f"{row['availability']:9} {', '.join(row['capabilities'])}")
    typer.echo(f"{result['total']} asset(s) in generation {result['generation']}")


@library_app.command("info")
def component_library_info(asset_id: str = typer.Argument(...),
                           json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library import resolver

    try:
        detail = resolver.inspect(asset_id)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))
    if json_output:
        _out(detail, True)
        return
    typer.echo(f"{detail['asset_id']} · {detail['kind']} · "
               f"{detail['package_version']} · digest {detail['digest'][:12]}")
    typer.echo(f"  doc {detail['documentation_status']} · "
               f"capabilities {', '.join(detail['capabilities'])}")
    typer.echo(f"  package {detail['package_path']}")
    if detail.get("documentation_gaps"):
        typer.echo(f"  gaps {', '.join(detail['documentation_gaps'])}")
    for row in detail.get("parameters") or []:
        typer.echo(f"  param {row.get('name')} = {row.get('value_min', row.get('value'))}"
                   f"..{row.get('value_max', '')} {row.get('unit', '')} "
                   f"[{row.get('status')}]")


@library_app.command("install")
def component_library_install(source: Path = typer.Argument(...),
                              provider: str = typer.Option("native_folder", "--provider"),
                              dry_run: bool = typer.Option(False, "--dry-run"),
                              json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library import management

    try:
        result = management.install(source, provider=provider, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - reported to the operator
        raise typer.Exit(_fail(exc, json_output))
    if json_output:
        _out(result, True)
        return
    typer.echo(f"provider {provider} · generation {result['generation']}")
    typer.echo(f"  installed {len(result.get('installed', []))} · "
               f"rejected {len(result.get('rejected', []))}")
    for row in result.get("rejected", []):
        typer.echo(f"  ! {row['asset_id']}: {row['error']}")


@library_app.command("enable")
def component_library_enable(asset_id: str = typer.Argument(...),
                             json_output: bool = typer.Option(False, "--json")) -> None:
    _set_enabled(asset_id, True, json_output)


@library_app.command("disable")
def component_library_disable(asset_id: str = typer.Argument(...),
                              json_output: bool = typer.Option(False, "--json")) -> None:
    _set_enabled(asset_id, False, json_output)


def _set_enabled(asset_id: str, enabled: bool, json_output: bool) -> None:
    from mota.library import management

    try:
        result = management.set_enabled(asset_id, enabled)
    except Exception as exc:  # noqa: BLE001
        raise typer.Exit(_fail(exc, json_output))
    _out(result, json_output,
         f"{asset_id} {'enabled' if enabled else 'disabled'} "
         f"in generation {result['generation']}")


@library_app.command("remove")
def component_library_remove(asset_id: str = typer.Argument(...),
                             version: str = typer.Option("", "--version"),
                             json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library import management

    try:
        result = management.remove(asset_id, version=version or None)
    except Exception as exc:  # noqa: BLE001
        raise typer.Exit(_fail(exc, json_output))
    if result.get("removed"):
        _out(result, json_output,
             f"Removed {asset_id} · generation {result['generation']}")
    else:
        typer.echo(f"Not removed: {asset_id} is required by "
                   f"{', '.join(result.get('blocked_by', []))}")


@library_app.command("refresh")
def component_library_refresh(json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library import management

    result = management.refresh()
    _out(result, json_output,
         f"Refreshed · {result['after']} packages · generation {result['generation']}"
         + (f" · {len(result['removed_missing'])} missing" if result["removed_missing"] else ""))


@library_app.command("gc")
def component_library_gc(apply: bool = typer.Option(False, "--apply"),
                         json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library import management

    result = management.gc(dry_run=not apply)
    _out(result, json_output,
         f"{result['count']} unreferenced package(s)"
         + (" (dry run)" if result["dry_run"] else " removed"))


@library_app.command("migrate")
def component_library_migrate(source: Path | None = typer.Option(None, "--source"),
                              builtin_only: bool = typer.Option(False, "--builtin-only"),
                              json_output: bool = typer.Option(False, "--json")) -> None:
    from mota.library.migration import migrate_builtin, migrate_kicad

    try:
        if builtin_only:
            result = migrate_builtin()
        else:
            result = migrate_kicad(old_root=source.expanduser() if source else None)
    except Exception as exc:  # noqa: BLE001
        raise typer.Exit(_fail(exc, json_output))
    if json_output:
        _out(result, True)
        return
    typer.echo(f"generation {result.get('generation')} · root {result.get('root')}")
    for key in ("components", "symbols", "footprints", "parsed", "unsupported",
                "failed", "packages", "conflicts"):
        if key in result:
            typer.echo(f"  {key}: {result[key]}")


@templates_app.command("list")
def templates_list(json_output: bool = typer.Option(False, "--json")) -> None:
    service, session = _require_session()
    result = service.dispatch("template.list", {}, session)
    if json_output:
        _out(result, True)
        return
    for template in result["templates"]:
        typer.echo(f"{template['template_id']:18} {template['title']:24} "
                   f"{template['status']:10} {template['component_count']} parts · "
                   f"{template['board'].get('width_mm')}x"
                   f"{template['board'].get('height_mm')} mm")


# ----------------------------------------------------------------------- projects
@project_app.command("create")
def project_create(name: str = typer.Option(..., prompt=True),
                   template: str = typer.Option(""),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.create", {
            "name": name, "template_id": template or None}, session)
        _out(result, json_output,
             f"Created {result['project_id']} ({result['current_revision_id']}).")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("list")
def project_list(archived: bool = typer.Option(False, "--archived"),
                 json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.list", {"archived": archived}, session)
        if json_output:
            _out(result, True)
            return
        for card in result["projects"]:
            typer.echo(f"{card['project_id']:24} {card['name'][:32]:32} "
                       f"{card['current_revision_id'] or '-':12} "
                       f"{card['review_status']:18} {card['updated_at']}")
        if not result["projects"]:
            typer.echo("No projects yet.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("open")
def project_open(project_id: str,
                 json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.open", {"project_id": project_id}, session)
        if json_output:
            _out(result, True)
            return
        typer.echo(f"{result['project']['name']} · {result['current_revision_id']} · "
                   f"{result['review_status'] if 'review_status' in result else result['revision']['review_status']} · "
                   f"{result['component_count']} components · {result['net_count']} nets")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("history")
def project_history(project_id: str,
                    json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("revision.list", {"project_id": project_id}, session)
        if json_output:
            _out(result, True)
            return
        for row in result["revisions"]:
            marker = "*" if row["is_current"] else " "
            typer.echo(f"{marker} {row['revision_id']} {row['created_at']} "
                       f"{row['review_status']:18} {row['summary'][:60]}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("duplicate")
def project_duplicate(project_id: str,
                      revision: str = typer.Option(""),
                      name: str = typer.Option(""),
                      json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.duplicate", {
            "source_project_id": project_id, "source_revision_id": revision or None,
            "name": name or None, "idempotency_key": "dup_" + secrets.token_hex(4)},
            session)
        _out(result, json_output, f"Duplicated to {result['project_id']}.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("archive")
def project_archive(project_id: str,
                    json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.archive", {"project_id": project_id}, session)
        _out(result, json_output, f"Archived {project_id}.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("restore-from-archive")
def project_restore(project_id: str,
                    json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.restore_from_archive",
                                  {"project_id": project_id}, session)
        _out(result, json_output, f"Restored {project_id} to Active.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("delete")
def project_delete(project_id: str,
                   confirm: str = typer.Option("", "--confirm"),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    if confirm != project_id:
        typer.echo("Permanent deletion requires --confirm <project_id>.", err=True)
        raise typer.Exit(EXIT_INPUT_ERROR)
    try:
        service, session = _require_session()
        result = service.dispatch("project.delete", {
            "project_id": project_id, "confirm_project_id": confirm}, session)
        _out(result, json_output, f"Deleted {project_id}.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("import")
def project_import(bundle_path: str,
                   name: str = typer.Option(""),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.import_bundle", {
            "file_path": bundle_path, "name": name or None,
            "idempotency_key": "import_" + secrets.token_hex(4)}, session)
        _out(result, json_output,
             f"Imported as {result['project_id']} ({result['revision_id']}) · "
             f"{result['review_status']}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@project_app.command("export")
def project_export(project_id: str,
                   revision: str = typer.Option("", "--revision"),
                   format: str = typer.Option("mota", "--format"),
                   output: str = typer.Option("./exports", "--output"),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("project.export", {
            "project_id": project_id, "revision_id": revision or None,
            "format": format, "destination_path": str(Path(output).expanduser())},
            session)
        _out(result, json_output,
             f"Exported {format} for {result['revision_id']} → "
             + ", ".join(f["path"] for f in result["files"]))
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


# --------------------------------------------------------------------------- build
@app.command()
def build(project: str = typer.Option(..., "--project"),
          template: str = typer.Option("", "--template"),
          prompt: str = typer.Option("", "--prompt"),
          requirements: str = typer.Option("", "--requirements"),
          accept_template_defaults: bool = typer.Option(False, "--accept-template-defaults"),
          param: list[str] = typer.Option([], "--set", help="key=value template parameter"),
          json_output: bool = typer.Option(False, "--json"),
          non_interactive: bool = typer.Option(False, "--non-interactive")) -> None:
    """Build a board from a template, a prompt or a requirement document."""
    try:
        service, session = _require_session()
        opened = service.dispatch("project.open", {"project_id": project}, session)
        expected = opened["current_revision_id"]
        parameters: dict[str, Any] = {}
        for item in param:
            if "=" not in item:
                raise MotaError(f"Invalid --set {item}; use key=value.", code="INVALID_REQUEST")
            key, value = item.split("=", 1)
            try:
                parameters[key] = float(value)
            except ValueError:
                parameters[key] = value
        if prompt and template:
            raise MotaError("Use either --template or --prompt, not both.",
                            code="INVALID_REQUEST")
        if not (template or prompt or requirements):
            raise MotaError("Provide --template, --prompt or --requirements.",
                            code="INVALID_REQUEST")
        if template:
            payload: dict[str, Any] = {
                "kind": "template", "template_id": template,
                "parameters": parameters,
                "accept_template_defaults": accept_template_defaults or bool(parameters)}
            if not parameters and not accept_template_defaults:
                from mota.catalog.templates import get_template

                recipe = get_template(template)
                defaults = {k: recipe.defaults[k] for k in recipe.required_fields
                            if k in recipe.defaults}
                if non_interactive or json_output:
                    _out({"state": "WAITING_FOR_USER", "template_id": template,
                          "questions": [{
                              "question_id": "template_defaults",
                              "prompt": f"Use the published defaults for {recipe.title}?",
                              "defaults": defaults,
                              "required_fields": recipe.required_fields,
                              "origin": "template"}]}, True)
                    raise typer.Exit(EXIT_INPUT_REQUIRED)
                summary = ", ".join(f"{k}={v}" for k, v in defaults.items())
                if typer.confirm(f"Use template defaults for {recipe.title} "
                                 f"({summary})?", default=True):
                    payload["accept_template_defaults"] = True
                else:
                    for field in recipe.required_fields:
                        raw = typer.prompt(field, default=str(recipe.defaults.get(field, "")))
                        try:
                            parameters[field] = float(raw)
                        except ValueError:
                            parameters[field] = raw
                    payload["parameters"] = parameters
            text = f"Build {template}"
        else:
            source_ids: list[str] = []
            text = prompt
            if requirements:
                source = service.dispatch("source.import", {
                    "project_id": project, "file_path": requirements}, session)
                source_ids.append(source["source_id"])
                if source.get("status") != "extracted":
                    typer.echo("Warning: the requirement document has pages that need "
                               "attention: " + "; ".join(source.get("warnings", [])), err=True)
            payload = {"kind": "prompt", "text": text or "Build from the attached requirements.",
                       "source_ids": source_ids}
        started = service.dispatch("run.start", {
            "project_id": project, "expected_revision_id": expected,
            "input": payload,
            "idempotency_key": "build_" + secrets.token_hex(6)}, session)
        _watch_run(service, session, started["run_id"], json_output, non_interactive)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


def _watch_run(service: ApplicationService, session: SessionContext, run_id: str,
               as_json: bool, non_interactive: bool) -> None:
    last_seq = 0
    last_state = None
    while True:
        data = service.dispatch("run.inspect", {"run_id": run_id,
                                                "after_seq": last_seq}, session)
        for event in data["events"]:
            last_seq = max(last_seq, event.get("seq", 0))
            if event["type"] == "run.state_changed":
                last_state = event["payload"].get("state")
                if not as_json:
                    typer.echo(f"[{last_state}] {event['payload'].get('label', '')}")
            elif event["type"] == "assistant.summary" and not as_json:
                text = event["payload"].get("text", "")
                if text:
                    typer.echo(f"  {text[:300]}")
            elif event["type"] == "validation.completed" and not as_json:
                counts = event["payload"].get("counts", {})
                typer.echo(f"  checks: {counts}")
            elif event["type"] == "tool.failed" and not as_json:
                err = event["payload"].get("error", {})
                typer.echo(f"  tool failed: {err.get('code')} {err.get('message', '')[:200]}")
            elif event["type"] == "run.completed" and not as_json:
                typer.echo(f"  {event['payload'].get('message', '')}")
            elif event["type"] == "user_input.required":
                if as_json:
                    continue
                _answer_question(service, session, run_id, event["payload"],
                                 non_interactive)
        run = data["run"]
        if run["state"] == "WAITING_FOR_USER" and not non_interactive and not as_json:
            time.sleep(0.2)
            continue
        if run["state"] in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            if as_json:
                _out({"run": run, "snapshot": data.get("snapshot"),
                      "last_events": data["events"][-6:]}, True)
            else:
                typer.echo(f"Run {run_id}: {run['state']}"
                           + (f" · {run.get('review_status')}" if run.get("review_status") else ""))
            if run["state"] == "SUCCEEDED":
                raise typer.Exit(EXIT_OK)
            if run["state"] == "PARTIAL":
                raise typer.Exit(EXIT_PARTIAL)
            if run["state"] == "CANCELLED":
                raise typer.Exit(EXIT_CANCELLED)
            if run["state"] == "FAILED":
                code = (run.get("error") or {}).get("code", "")
                raise typer.Exit(EXIT_UNAVAILABLE if code.startswith("MODEL_") else EXIT_FAILED)
            raise typer.Exit(EXIT_FAILED)
        if run["state"] == "WAITING_FOR_USER":
            if as_json or non_interactive:
                questions = data.get("questions", [])
                pending = [q for q in questions if not q.get("consumed_at")]
                _out({"run_id": run_id, "state": "WAITING_FOR_USER",
                      "questions": pending}, as_json)
                raise typer.Exit(EXIT_INPUT_REQUIRED)
        time.sleep(0.25)


def _answer_question(service: ApplicationService, session: SessionContext, run_id: str,
                     payload: dict[str, Any], non_interactive: bool) -> None:
    if non_interactive:
        raise typer.Exit(EXIT_INPUT_REQUIRED)
    typer.echo("")
    typer.echo(payload.get("prompt", "Input required"))
    choices = payload.get("choices") or []
    for index, choice in enumerate(choices, start=1):
        typer.echo(f"  {index}. {choice.get('label')} — {choice.get('description', '')}")
    answer_value = typer.prompt("Answer")
    answer: dict[str, Any] = {"value": answer_value}
    if choices and answer_value.isdigit():
        index = int(answer_value) - 1
        if 0 <= index < len(choices):
            answer["choice"] = choices[index].get("label")
    service.dispatch("run.answer", {"run_id": run_id,
                                    "question_id": payload.get("question_id"),
                                    "answer": answer}, session)


# ----------------------------------------------------------------------------- design
@design_app.command("patch")
def design_patch(project: str = typer.Option(..., "--project"),
                 operations: Path = typer.Option(..., "--operations",
                                                 help="JSON file: list or {operations: [...]}"),
                 expected_revision: str = typer.Option("", "--expected-revision"),
                 json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        payload = json.loads(operations.expanduser().read_text(encoding="utf-8"))
        raw = payload.get("operations", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw, list) or not raw:
            typer.echo("Operations file must contain a non-empty list.", err=True)
            raise typer.Exit(EXIT_INPUT_ERROR)
        service, session = _require_session()
        opened = service.dispatch("project.open", {"project_id": project}, session)
        result = service.dispatch("design.patch", {
            "project_id": project,
            "expected_revision_id": expected_revision or opened["current_revision_id"],
            "operations": raw,
            "idempotency_key": f"cli_patch_{time.time_ns()}"}, session)
        _out(result, json_output,
             f"{result['commit_status']} · revision {result['revision_id']} · "
             f"{result['review_status']} · {result['diff']['summary']}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


def _design_commit(project: str, operations: list[dict[str, Any]],
                   expected_revision: str, as_json: bool) -> None:
    service, session = _require_session()
    opened = service.dispatch("project.open", {"project_id": project}, session)
    result = service.dispatch("design.patch", {
        "project_id": project,
        "expected_revision_id": expected_revision or opened["current_revision_id"],
        "operations": operations,
        "idempotency_key": f"cli_patch_{time.time_ns()}"}, session)
    _out(result, as_json,
         f"{result['commit_status']} · revision {result['revision_id']} · "
         f"{result['review_status']} · {result['diff']['summary']}")


@design_app.command("move")
def design_move(project: str = typer.Option(..., "--project"),
                component: str = typer.Option(..., "--component"),
                x_mm: float = typer.Option(..., "--x-mm"),
                y_mm: float = typer.Option(..., "--y-mm"),
                rotation_deg: int = typer.Option(0, "--rotation-deg"),
                side: str = typer.Option("", "--side"),
                expected_revision: str = typer.Option("", "--expected-revision"),
                json_output: bool = typer.Option(False, "--json")) -> None:
    """Move one footprint. The desktop canvas uses the same design.patch path."""

    try:
        operation: dict[str, Any] = {
            "op": "place_footprint", "component_id": component,
            "x_mm": x_mm, "y_mm": y_mm,
            "rotation_udeg": int(rotation_deg) * 1_000_000}
        if side:
            operation["side"] = side
        _design_commit(project, [operation], expected_revision, json_output)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@design_app.command("lock")
def design_lock(project: str = typer.Option(..., "--project"),
                component: str = typer.Option(..., "--component"),
                unlock: bool = typer.Option(False, "--unlock"),
                expected_revision: str = typer.Option("", "--expected-revision"),
                json_output: bool = typer.Option(False, "--json")) -> None:
    """Lock or unlock a component through the shared design.patch operation."""

    try:
        _design_commit(project, [{"op": "lock_component",
                                  "component_id": component,
                                  "locked": not unlock}],
                       expected_revision, json_output)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


def _design_history(project: str, method: str, as_json: bool) -> None:
    service, session = _require_session()
    result = service.dispatch(method, {"project_id": project}, session)
    _out(result, as_json,
         f"{method.split('.')[-1]} → revision {result.get('revision_id')}"
         if result else f"{method.split('.')[-1]}: no change")


@design_app.command("undo")
def design_undo(project: str = typer.Option(..., "--project"),
                json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        _design_history(project, "design.undo", json_output)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@design_app.command("redo")
def design_redo(project: str = typer.Option(..., "--project"),
                json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        _design_history(project, "design.redo", json_output)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


# ----------------------------------------------------------------------------- run
@run_app.command("show")
def run_show(run_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        data = service.dispatch("run.inspect", {"run_id": run_id}, session)
        if json_output:
            _out(data, True)
            return
        run = data["run"]
        typer.echo(f"{run['run_id']} · {run['state']} · base {run.get('base_revision_id')} "
                   f"· draft seq {run.get('draft_seq')}")
        for event in data["events"][-20:]:
            typer.echo(f"  {event['seq']:04d} {event['type']:26} "
                       f"{str(event['payload'])[:100]}")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@run_app.command("answer")
def run_answer(run_id: str,
               question: str = typer.Option(..., "--question"),
               value: str = typer.Option(..., "--value"),
               json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("run.answer", {
            "run_id": run_id, "question_id": question,
            "answer": {"value": value}}, session)
        _out(result, json_output, f"Accepted answer for {run_id}.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@run_app.command("resume")
def run_resume(run_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        data = service.dispatch("run.inspect", {"run_id": run_id}, session)
        project_id = data["run"]["project_id"]
        opened = service.dispatch("project.open", {"project_id": project_id}, session)
        result = service.dispatch("run.resume", {
            "run_id": run_id, "expected_revision_id": opened["current_revision_id"]},
            session)
        _out(result, json_output,
             f"Resumed as {result['run_id']} (parent {run_id}).")
        _watch_run(service, session, result["run_id"], json_output, False)
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@run_app.command("cancel")
def run_cancel(run_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("run.cancel", {"run_id": run_id}, session)
        if result.get("outcome") == "already_committed":
            _out(result, json_output,
                 f"Revision {result['committed_revision_id']} was already saved; the run "
                 f"was not presented as cancelled.")
            raise typer.Exit(EXIT_OK)
        _out(result, json_output, "Cancellation requested.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


# -------------------------------------------------------------------------- memory
@memory_app.command("list")
def memory_list(layer: str = typer.Option("long_term", "--layer"),
                project: str = typer.Option("", "--project"),
                json_output: bool = typer.Option(False, "--json")) -> None:
    try:
        service, session = _require_session()
        result = service.dispatch("memory.list", {
            "layer": layer, "project_id": project or None}, session)
        if json_output:
            _out(result, True)
            return
        for entry in result["entries"]:
            typer.echo(f"{entry['entry_id']:14} {entry['scope']:8} "
                       f"{entry['status']:16} {entry['summary'][:60]}")
        if not result["entries"]:
            typer.echo("No entries.")
    except MotaError as exc:
        raise typer.Exit(_fail(exc, json_output))


@memory_app.command("show")
def memory_show(layer: str = typer.Option("experience", "--layer"),
                json_output: bool = typer.Option(False, "--json")) -> None:
    memory_list(layer=layer, json_output=json_output)


# -------------------------------------------------------------------------- benchmark
@benchmark_app.command("run")
def benchmark_run(
        suite: str = typer.Option(..., "--suite", help="Frozen suite name"),
        report: Path = typer.Option(Path("./reports"), "--report",
                                    help="Report directory"),
        json_output: bool = typer.Option(False, "--json")) -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[3]
    runner = root / "benchmarks" / "run.py"
    if not runner.is_file():
        typer.echo(f"Error [BENCHMARK_SUITE_NOT_FOUND]: {runner}", err=True)
        raise typer.Exit(code=EXIT_INPUT_ERROR)
    spec = importlib.util.spec_from_file_location("mota_benchmarks_run", runner)
    if spec is None or spec.loader is None:
        typer.echo("Error [BENCHMARK_SUITE_NOT_FOUND]: cannot load runner",
                   err=True)
        raise typer.Exit(code=EXIT_INPUT_ERROR)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        result = module.run_suite(suite, report)
    except Exception as exc:  # noqa: BLE001 - CLI reports benchmark failure
        typer.echo(f"Error [BENCHMARK_FAILED]: {exc}", err=True)
        raise typer.Exit(code=EXIT_FAILED) from exc
    if json_output:
        _out(result, True)
        return
    aggregate = result["aggregate"]
    typer.echo(
        f"suite {suite}: {aggregate['cases']} cases, "
        f"routed {aggregate['completed_targets']}/{aggregate['requested_targets']}, "
        f"unrouted {aggregate['unrouted_targets']}, U={aggregate['u_total']}, "
        f"frozen={'ok' if aggregate['frozen_hashes_ok'] else 'MISMATCH'}")
    typer.echo(f"report: {Path(report) / (suite + '.md')}")


if __name__ == "__main__":
    app()
