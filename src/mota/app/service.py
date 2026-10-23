"""ApplicationService: the single command surface shared by CLI and desktop."""

from __future__ import annotations

import base64
import json
import secrets
from pathlib import Path
from typing import Any, Callable

from mota.catalog import service as catalog_service
from mota.catalog.sync import catalog_status, sync_local_tree, sync_official
from mota.library import catalog as library_catalog
from mota.app.config import ConfigService
from mota.app.design import DesignService
from mota.app.runs import RunService
from mota.app.scenes import draft_scene_json, find_artifact, write_revision_scenes
from mota.app.state import SessionContext, UserScope
from mota.app.tasks import TaskService
from mota.auth.credentials import SecretStore
from mota.auth.service import AuthService, username_key
from mota.domain.models import (
    BoardIR, BuildManifest, CircuitIR, DesignPatch, DesignVersion, PatchOperation,
    ProjectRevisionMeta, RequirementSpec, RunRecord, SchematicIR, ValidationReport,
    utcnow,
)
from mota.domain.units import mm
from mota.errors import (
    ArtifactInvalid, AuthRequired, BundleInvalid, ConfigConflict, IdempotencyConflict,
    InvalidRequest, ModelUnavailable, NotFound, ProjectBusy, RevisionConflict, SaveFailed,
)
from mota.generation.board import rebuild
from mota.ingest import documents
from mota.memory.service import MemoryService
from mota.projects import bundle as bundle_service
from mota.projects.export import export_capabilities, export_revision
from mota.projects.store import ProjectStore
from mota.validation.reports import validate_all
from mota.workspace import paths
from mota.workspace.atomic import read_json, sha256_file, write_json_atomic


class ApplicationService:
    def __init__(self, root: Path | None = None,
                 emit_event: Callable[[dict[str, Any]], None] | None = None,
                 emit_task: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.root = root or paths.data_root()
        self.auth = AuthService(self.root)
        self.secrets = SecretStore()
        self.runs = RunService(self.root, emit=emit_event, secrets=self.secrets)
        self.tasks = TaskService(self.root, emit=emit_task)
        self.design = DesignService()
        self._event_sink = emit_event
        self._session_hooks: list[Callable[[SessionContext], None]] = []

    # ------------------------------------------------------------------ helpers
    def _require(self, session: SessionContext | None) -> SessionContext:
        if session is None or not session.user_id:
            raise AuthRequired("Sign in to continue.")
        return session

    def _store(self, session: SessionContext) -> ProjectStore:
        ctx = self._require(session)
        return ProjectStore(ctx.scope(), self.root)

    def _memory(self, session: SessionContext) -> MemoryService:
        ctx = self._require(session)
        return MemoryService(ctx.scope(), self.root)

    # ------------------------------------------------------------------ dispatch
    def dispatch(self, method: str, params: dict[str, Any],
                 session: SessionContext | None) -> dict[str, Any]:
        handler = getattr(self, "cmd_" + method.replace(".", "_"), None)
        if handler is None:
            raise NotFound(f"Unknown method {method}.", code="METHOD_NOT_FOUND")
        return handler(params, session)

    # ---------------------------------------------------------------- engine/auth
    def cmd_engine_hello(self, params: dict[str, Any], session) -> dict[str, Any]:
        from mota.protocol.schemas import hello_payload

        return hello_payload()

    def cmd_engine_health(self, params: dict[str, Any], session) -> dict[str, Any]:
        return {"ok": True, "data_root": str(self.root), "timestamp": utcnow()}

    def cmd_engine_shutdown(self, params: dict[str, Any], session) -> dict[str, Any]:
        return {"shutting_down": True}

    def cmd_auth_register(self, params: dict[str, Any], session) -> dict[str, Any]:
        profile, ctx = self.auth.register(params.get("username", ""),
                                         params.get("password", ""),
                                         params.get("display_name"))
        return {"account_created": True, "profile": profile["profile"] if "profile" in profile else profile,
                "session": self._session_payload(ctx)}

    def cmd_auth_login(self, params: dict[str, Any], session) -> dict[str, Any]:
        profile, ctx = self.auth.login(params.get("username", ""),
                                      params.get("password", ""))
        remember = bool(params.get("remember_me"))
        payload = {"profile": profile["profile"] if "profile" in profile else profile,
                   "session": self._session_payload(ctx)}
        if remember:
            token = self._issue_remember_token(ctx.user_id)
            payload["credential_ref"] = token
            payload["credential_storage"] = self.secrets.storage
        return payload

    def cmd_auth_resume(self, params: dict[str, Any], session) -> dict[str, Any]:
        token = params.get("credential_ref") or ""
        user_id = self._resolve_remember_token(token)
        if not user_id:
            raise AuthRequired("Stored credentials expired. Sign in again.",
                               code="AUTH_FAILED")
        profile = self.auth.get_public(user_id)
        ctx = SessionContext(session_id="session_" + secrets.token_hex(8),
                             user_id=user_id, username=profile["username"])
        return {"profile": profile, "session": self._session_payload(ctx)}

    def cmd_auth_logout(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        cancelled = []
        for run in self.runs.active_runs(ctx.scope()):
            try:
                self.runs.cancel(ctx.scope(), run["run_id"])
                cancelled.append(run["run_id"])
            except Exception:
                pass
        self.secrets.clear_session()
        return {"logged_out": True, "cancelled_runs": cancelled}

    def cmd_auth_status(self, params: dict[str, Any], session) -> dict[str, Any]:
        if session is None:
            return {"authenticated": False}
        return {"authenticated": True, "profile": self.auth.get_public(session.user_id),
                "session": self._session_payload(session)}

    def _session_payload(self, ctx: SessionContext) -> dict[str, Any]:
        return {"session_id": ctx.session_id, "user_id": ctx.user_id,
                "username": ctx.username, "epoch": ctx.epoch,
                "secret_storage": self.secrets.storage}

    def _issue_remember_token(self, user_id: str) -> str:
        import hashlib

        token = secrets.token_urlsafe(32)
        path = paths.app_dir(self.root) / "remember_tokens.json"
        data = read_json(path, default={"schema_version": 1, "tokens": {}}) or {}
        digest = hashlib.sha256(token.encode()).hexdigest()
        data.setdefault("tokens", {})[digest] = {"user_id": user_id,
                                                "created_at": utcnow()}
        write_json_atomic(path, data)
        self.secrets.set(user_id, "remember_token", token)
        return token

    def _resolve_remember_token(self, token: str) -> str | None:
        import hashlib

        if not token:
            return None
        path = paths.app_dir(self.root) / "remember_tokens.json"
        data = read_json(path, default={"tokens": {}}) or {}
        digest = hashlib.sha256(token.encode()).hexdigest()
        entry = data.get("tokens", {}).get(digest)
        return entry.get("user_id") if entry else None

    # -------------------------------------------------------------------- config
    def cmd_config_get(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        cfg = ConfigService(ctx.scope(), self.secrets, self.root).get()
        return {"config": cfg.model_dump(),
                "secret_storage_capabilities": {
                    "keychain": self.secrets.storage == "keychain",
                    "session_only": True}}

    def cmd_config_update(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        service = ConfigService(ctx.scope(), self.secrets, self.root)
        cfg, meta = service.update(int(params.get("expected_config_version", 0)),
                                   dict(params.get("changes", {})),
                                   params.get("idempotency_key", ""))
        return {"config": cfg.model_dump(), **meta}

    def cmd_config_probe_model(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        service = ConfigService(ctx.scope(), self.secrets, self.root)
        cfg = service.get()
        if int(params.get("expected_config_version", cfg.config_version)) != cfg.config_version:
            raise ConfigConflict("Settings changed. Reload before probing.",
                                 data={"current_config_version": cfg.config_version})
        profile_id = params.get("profile_id") or cfg.default_profile_id
        profile, key = service.get_profile(profile_id)
        if not key:
            raise ModelUnavailable(
                f"AI connection “{profile.name}” has no API key.",
                code="MODEL_UNAVAILABLE", data={"profile_id": profile.profile_id})
        suites = params.get("test_suite") or params.get("test_suites") or ["text"]
        if isinstance(suites, str):
            suites = [suites]
        suites = [s for s in suites if s in ("text", "tool_roundtrip", "vision")]
        if not suites:
            suites = ["text"]
        binding = service.build_binding(profile)
        version = cfg.config_version

        from mota.agent.probe import run_probe

        def runner(progress):
            progress({"stage": "probe_start", "message": f"Probing {profile.name}",
                      "profile_id": profile.profile_id, "suites": suites})
            result = run_probe(binding, key, suites, progress)
            result["config_version"] = version
            service.record_probe_result(profile.profile_id, result.get("suites", []),
                                        version)
            return result

        return self.tasks.start(ctx.scope(), "model_probe", runner,
                                session.epoch if session else 0)

    # -------------------------------------------------------------------- catalog
    def cmd_catalog_search(self, params: dict[str, Any], session) -> dict[str, Any]:
        return catalog_service.search(params.get("query", ""),
                                      category=params.get("category"),
                                      package=params.get("package"),
                                      capability=params.get("capability"),
                                      limit=int(params.get("limit", 20)),
                                      offset=int(params.get("offset", 0)),
                                      root=self.root)

    def cmd_catalog_inspect(self, params: dict[str, Any], session) -> dict[str, Any]:
        asset_id = params.get("asset_id", "")
        sections = params.get("sections")
        return catalog_service.inspect(asset_id, sections=sections)

    def cmd_catalog_status(self, params: dict[str, Any], session) -> dict[str, Any]:
        status = catalog_status(params.get("snapshot_id"))
        try:
            status["component_library"] = catalog_service.resolver_status()
        except Exception:  # noqa: BLE001 - status must stay available
            status["component_library"] = None
        return status

    def cmd_catalog_sync(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        source = params.get("source", "kicad")
        library_dir = params.get("library_dir")

        def runner(progress):
            if source == "builtin":
                progress({"stage": "builtin", "message": "Builtin snapshot is already active"})
                return {"snapshot_id": "mota_builtin_v1", "kind": "builtin",
                        "coverage": {"recipe_ready": 3, "parts": 13}}
            if library_dir:
                coverage = sync_local_tree(Path(library_dir).expanduser(),
                                           progress=progress)
                return {"snapshot_id": coverage.snapshot_id, "kind": "local",
                        "coverage": coverage.to_dict()}
            coverage = sync_official(params.get("release_ref", "master"),
                                     max_bytes=int(params.get("max_bytes", 400 * 1024 * 1024)),
                                     progress=progress)
            return {"snapshot_id": coverage.snapshot_id, "kind": "official",
                    "coverage": coverage.to_dict()}

        return self.tasks.start(ctx.scope(), "catalog_sync", runner,
                                session.epoch if session else 0)

    # -------------------------------------------------------------------- library
    def cmd_library_import(self, params: dict[str, Any], session) -> dict[str, Any]:
        self._require(session)
        source = library_catalog.SourceSpec(
            source_id=params.get("source_id", ""),
            path=params.get("path", ""),
            version=params.get("version", "unknown"),
            license=params.get("license", "unknown"),
            license_url=params.get("license_url", ""),
            format=params.get("format", "auto"),
            library_name=params.get("library_name") or None)
        report = library_catalog.import_library(
            source, root=self.root,
            store_content=bool(params.get("store_content", True)))
        return report.model_dump()

    def cmd_library_status(self, params: dict[str, Any], session) -> dict[str, Any]:
        return library_catalog.library_status(self.root)

    def cmd_library_search(self, params: dict[str, Any], session) -> dict[str, Any]:
        return library_catalog.search_assets(
            params.get("query", ""), root=self.root,
            source_id=params.get("source_id"), kind=params.get("kind"),
            status=params.get("status"),
            limit=int(params.get("limit", 20)),
            offset=int(params.get("offset", 0)))

    def cmd_library_inspect(self, params: dict[str, Any], session) -> dict[str, Any]:
        return library_catalog.inspect_asset(params.get("asset_id", ""), root=self.root)

    def cmd_library_preview(self, params: dict[str, Any], session) -> dict[str, Any]:
        asset_id = params.get("asset_id", "")
        return {"asset_id": asset_id,
                "svg": library_catalog.preview_asset(asset_id, root=self.root)}

    def cmd_library_rollback(self, params: dict[str, Any], session) -> dict[str, Any]:
        self._require(session)
        return library_catalog.rollback_library(params.get("source_id", ""),
                                                root=self.root)

    def cmd_template_list(self, params: dict[str, Any], session) -> dict[str, Any]:
        return {"templates": catalog_service.templates()}

    def cmd_template_inspect(self, params: dict[str, Any], session) -> dict[str, Any]:
        return catalog_service.template_inspect(params.get("template_id", ""))

    def cmd_task_inspect(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        return self.tasks.inspect(ctx.scope(), params.get("task_id", ""),
                                  params.get("after_seq"),
                                  session.epoch if session else 0)

    # -------------------------------------------------------------------- sources
    def cmd_source_import(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        return documents.import_source(store, params.get("project_id", ""),
                                       params.get("file_path", ""))

    def cmd_source_list(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        return {"sources": documents.list_sources(store, params.get("project_id", ""))}

    # ------------------------------------------------------------------- projects
    def _empty_design(self) -> tuple[RequirementSpec, CircuitIR, SchematicIR, BoardIR,
                                     ValidationReport]:
        width, height = mm(60.0), mm(40.0)
        requirement = RequirementSpec(
            purpose="Blank project", input_kind="template",
            board_size={"width_mm": 60.0, "height_mm": 40.0})
        circuit = CircuitIR()
        schematic = SchematicIR(page_w_nm=mm(297.0), page_h_nm=mm(210.0),
                                title="Mota schematic")
        # A blank board has real extents but no invented Edge.Cuts outline.
        board = BoardIR(outline=[], width_nm=width, height_nm=height,
                        thickness_nm=mm(1.6))
        checks = ValidationReport(project_id="", design_version=DesignVersion.of_draft("initial", 0),
                                  design_version_key="draft:initial:0", review_status="DRAFT")
        return requirement, circuit, schematic, board, checks

    def cmd_project_create(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        name = (params.get("name") or "Untitled board").strip()
        requirement, circuit, schematic, board, checks = self._empty_design()
        meta = store.create(name, requirement, circuit, schematic, board, checks,
                            template_id=params.get("template_id"),
                            source_kind=params.get("source_kind", "blank"))
        write_revision_scenes(store.load_revision(meta.project_id, meta.revision_id), store)
        project = store.get_card(meta.project_id)
        return {"project": project.model_dump(),
                "project_id": meta.project_id,
                "current_revision_id": meta.revision_id,
                "review_status": meta.review_status,
                "views": {"available": ["schematic", "pcb2d", "pcb3d"],
                          "note": "No circuit objects yet."}}

    def cmd_project_open(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        try:
            self.runs.recover_orphans(ctx.scope())
        except Exception:
            pass
        project_id = params.get("project_id", "")
        card = store.get_card(project_id)
        self._restore_project_assets(store, project_id)
        snapshot = store.open_current(project_id)
        runs = [r for r in self.runs.active_runs(ctx.scope())
                if r.get("project_id") == project_id]
        return {
            "project": card.model_dump(),
            "current_revision_id": snapshot.revision.revision_id,
            "revision": snapshot.revision.model_dump(),
            "requirement": snapshot.requirement.model_dump(),
            "checks": snapshot.checks.model_dump(),
            "checks_summary": snapshot.checks.counts,
            "component_count": len(snapshot.circuit.components),
            "net_count": len(snapshot.circuit.nets),
            "views": {"available": ["schematic", "pcb2d", "pcb3d"],
                      "unavailable": []},
            "active_run": runs[0] if runs else None,
            "read_only": False,
            "capabilities": self._capabilities(),
        }

    def _restore_project_assets(self, store, project_id: str) -> None:
        """Rehydrate library packages pinned by a project's lock.

        Uninstalling a shared package must not make a saved revision
        unresolvable: the revision's vendored snapshot is copied back before
        anything reads its assets.
        """
        try:
            from mota.library import resolver as library_resolver
            library_resolver.ensure_project_assets(store.project_dir(project_id))
        except Exception:  # noqa: BLE001 - opening a project must not fail here
            return

    def _capabilities(self) -> dict[str, Any]:
        return {
            "export": export_capabilities(),
            "model_configured": False,
            "template_build": True,
            "interactive_edit": True,
            "memory_layers": ["short_term", "long_term", "experience"],
        }

    def cmd_project_watch(self, params: dict[str, Any], session) -> dict[str, Any]:
        """Cheap revision probe so an open canvas can follow real commits.

        Returns only identity/counts; callers that see a new revision reload
        through `project.open`.
        """
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        card = store.get_card(project_id)
        snapshot = store.open_current(project_id)
        return {
            "project_id": project_id,
            "current_revision_id": snapshot.revision.revision_id,
            "review_status": snapshot.revision.review_status,
            "component_count": len(snapshot.circuit.components),
            "net_count": len(snapshot.circuit.nets),
            "updated_at": card.updated_at,
        }

    def cmd_project_list(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        try:
            self.runs.recover_orphans(ctx.scope())
        except Exception:
            pass
        cards = store.list_projects(archived=bool(params.get("archived")))
        return {"projects": [c.model_dump() for c in cards],
                "archived": bool(params.get("archived"))}

    def cmd_project_rename(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        return {"project": store.rename(params["project_id"],
                                        params.get("name", "")).model_dump()}

    def cmd_project_archive(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        return {"project": store.set_archived(params["project_id"], True).model_dump()}

    def cmd_project_restore_from_archive(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        return {"project": store.set_archived(params["project_id"], False).model_dump()}

    def cmd_project_delete(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        store.delete(params["project_id"], params.get("confirm_project_id", ""))
        return {"deleted": True, "project_id": params["project_id"]}

    def cmd_project_duplicate(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        source_id = params["source_project_id"]
        source_rev = params.get("source_revision_id") or store.current_revision_id(source_id)
        if not source_rev:
            raise NotFound("Source project has no revision.", code="NO_CURRENT_REVISION")
        snapshot = store.load_revision(source_id, source_rev)
        from mota.projects.bundle import rekey

        circuit, schematic, board = rekey(snapshot.circuit, snapshot.schematic,
                                          snapshot.board, secrets.token_hex(3))
        name = (params.get("name") or f"{store.get_card(source_id).name} copy").strip()
        new_project_id = "project_" + secrets.token_hex(8)
        checks = validate_all(new_project_id, DesignVersion.of_draft("dup", 0),
                              snapshot.requirement, circuit, schematic, board,
                              self._recipe_for(snapshot.requirement))
        meta = store.create(name, snapshot.requirement, circuit, schematic, board, checks,
                            template_id=snapshot.requirement.template_id,
                            source_kind="duplicate", commit_source="import",
                            project_id=new_project_id)
        write_revision_scenes(store.load_revision(meta.project_id, meta.revision_id), store)
        return {"project_id": meta.project_id, "revision_id": meta.revision_id,
                "source_project_id": source_id, "source_revision_id": source_rev,
                "review_status": meta.review_status}

    def cmd_project_import_bundle(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        return bundle_service.import_bundle(store, params.get("file_path", ""),
                                            params.get("name"),
                                            params.get("idempotency_key", ""))

    def cmd_project_export(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        revision_id = params.get("revision_id") or store.current_revision_id(project_id)
        if not revision_id:
            raise NotFound("Project has no revision.", code="NO_CURRENT_REVISION")
        snapshot = store.load_revision(project_id, revision_id)
        destination = Path(params.get("destination_path") or
                           (paths.workspace_dir(ctx.user_id, self.root) / "exports"))
        result = export_revision(snapshot, params.get("format", "mota"), destination)
        return result

    # ----------------------------------------------------------------------- runs
    def cmd_run_start(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        payload = dict(params.get("input") or {})
        kind = payload.get("kind", "template")
        if kind == "template":
            if not payload.get("template_id"):
                raise InvalidRequest("Template builds need template_id.", code="MISSING_TEMPLATE")
            if not payload.get("accept_template_defaults") and not payload.get("parameters"):
                raise InvalidRequest(
                    "Explicit template defaults are required. Pass parameters or "
                    "accept_template_defaults=true.", code="MISSING_REQUIRED_FIELD")
        if kind != "template":
            cfg = ConfigService(ctx.scope(), self.secrets, self.root).get()
            profile_id = payload.get("profile_id") or cfg.default_profile_id
            profile = next((p for p in cfg.profiles if p.profile_id == profile_id), None)
            key = self.secrets.get_profile_key(ctx.scope(), profile_id or "",
                                               profile.provider if profile else "openai")
            if not key:
                raise ModelUnavailable(
                    f"AI connection “{profile.name if profile else profile_id or 'default'}” "
                    f"has no API key. Add one in AI connections, or use a template build.",
                    code="MODEL_UNAVAILABLE",
                    data={"profile_id": profile_id,
                          "next_steps": ["Open AI connections", "Use a template"]})
        return self.runs.start(ctx.scope(), params.get("project_id", ""),
                               params.get("expected_revision_id"), payload,
                               params.get("idempotency_key", ""),
                               session.epoch if session else 0,
                               session.session_id if session else "session_cli")

    def cmd_run_answer(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        return self.runs.answer(ctx.scope(), params.get("run_id", ""),
                                params.get("question_id", ""),
                                dict(params.get("answer") or {}),
                                session.epoch if session else 0)

    def cmd_run_cancel(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        return self.runs.cancel(ctx.scope(), params.get("run_id", ""))

    def cmd_run_resume(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        return self.runs.resume(ctx.scope(), params.get("run_id", ""),
                                params.get("expected_revision_id"),
                                session.epoch if session else 0,
                                session.session_id if session else "session_cli")

    def cmd_run_inspect(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        return self.runs.inspect(ctx.scope(), params.get("run_id", ""),
                                 params.get("after_seq"),
                                 session.epoch if session else 0)

    # ---------------------------------------------------------------------- edits
    def cmd_design_patch(self, params: dict[str, Any], session) -> dict[str, Any]:
        import hashlib

        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        if store.has_active_run(project_id):
            raise ProjectBusy("Finish or cancel the current build to edit.",
                              data={"project_id": project_id})
        expected = params.get("expected_revision_id") or store.current_revision_id(project_id)
        operations_raw = params.get("operations", [])
        request_hash = hashlib.sha256(json.dumps({
            "project_id": project_id, "expected_revision_id": expected,
            "operations": operations_raw}, sort_keys=True,
            ensure_ascii=False).encode("utf-8")).hexdigest()
        idempotency_key = params.get("idempotency_key")
        if idempotency_key:
            index = read_json(store.project_dir(project_id) / "commit_index.json",
                              default={"entries": []}) or {}
            for entry in index.get("entries", []):
                if entry.get("idempotency_key") != idempotency_key:
                    continue
                if entry.get("request_hash") and entry["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "This idempotency key was used for a different request.",
                        code="IDEMPOTENCY_CONFLICT")
                if entry.get("status") == "COMMITTED" and entry.get("revision_id"):
                    revision_id = entry["revision_id"]
                    meta = store.load_revision(project_id, revision_id).revision
                    return {"commit_status": "COMMITTED", "project_id": project_id,
                            "revision_id": revision_id,
                            "design_version": DesignVersion.of_revision(revision_id).to_param(),
                            "review_status": meta.review_status,
                            "diff": {"added": [], "modified": [], "deleted": [],
                                     "summary": "Idempotent replay"},
                            "checks_summary": meta.checks_summary,
                            "idempotent_replay": True,
                            "session_epoch": session.epoch if session else 0}
                raise SaveFailed("The previous commit is prepared but not committed.",
                                 code="COMMIT_PREPARED")
        if expected != store.current_revision_id(project_id):
            raise RevisionConflict("This project changed. Reload the latest revision.",
                                   data={"current_revision_id":
                                         store.current_revision_id(project_id)})
        from mota.agent.tools import operation_from_json

        operations = [operation_from_json(op) for op in operations_raw]
        if not operations:
            raise InvalidRequest("design.patch needs at least one operation.")
        draft = store.begin_draft(project_id, expected, None)
        draft.draft_seq += 1
        version = DesignVersion.of_draft(draft.draft_id, draft.draft_seq)
        outcome = self.design.apply(draft, DesignPatch(operations=operations),
                                    project_id=project_id, version=version)
        draft.circuit = outcome.circuit
        draft.requirement = outcome.requirement
        draft.schematic = outcome.schematic
        draft.board = outcome.board
        draft.checks = outcome.checks
        store.save_draft(draft)
        meta = store.commit(draft, expected, run_id=None,
                            idempotency_key=idempotency_key,
                            summary=outcome.summary, source="edit",
                            request_hash=request_hash)
        snapshot = store.load_revision(project_id, meta.revision_id)
        write_revision_scenes(snapshot, store)
        self._history_push(store, project_id, expected, meta.revision_id)
        return {"commit_status": "COMMITTED", "project_id": project_id,
                "revision_id": meta.revision_id,
                "design_version": DesignVersion.of_revision(meta.revision_id).to_param(),
                "review_status": meta.review_status, "diff": {
                    "added": outcome.added, "modified": outcome.modified,
                    "deleted": outcome.deleted, "summary": outcome.summary},
                "checks_summary": meta.checks_summary,
                "session_epoch": session.epoch if session else 0}

    def cmd_design_undo(self, params: dict[str, Any], session) -> dict[str, Any]:
        return self._undo_redo(params, session, direction="undo")

    def cmd_design_redo(self, params: dict[str, Any], session) -> dict[str, Any]:
        return self._undo_redo(params, session, direction="redo")

    def _history_path(self, store: ProjectStore, project_id: str) -> Path:
        return store.project_dir(project_id) / "edit_history.json"

    def _history_push(self, store: ProjectStore, project_id: str, current: str | None,
                      new_revision: str) -> None:
        path = self._history_path(store, project_id)
        history = read_json(path, default={"undo": [], "redo": []}) or \
            {"undo": [], "redo": []}
        if current:
            history.setdefault("undo", []).append(current)
        history["redo"] = []
        history.setdefault("last_revision", new_revision)
        write_json_atomic(path, history)

    def _undo_redo(self, params: dict[str, Any], session: SessionContext,
                   direction: str) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        if store.has_active_run(project_id):
            raise ProjectBusy("Finish or cancel the current build to edit.",
                              data={"project_id": project_id})
        history_path = self._history_path(store, project_id)
        history = read_json(history_path, default={"undo": [], "redo": []}) or \
            {"undo": [], "redo": []}
        stack = history.setdefault(direction, [])
        current = store.current_revision_id(project_id)
        if direction == "undo" and not stack:
            snapshot = store.open_current(project_id)
            parent = snapshot.revision.parent_revision_id
            if parent:
                stack.append(parent)
        if direction == "undo" and not stack:
            raise InvalidRequest("Nothing to undo.", code="NOTHING_TO_UNDO")
        if direction == "redo" and not stack:
            raise InvalidRequest("Nothing to redo.", code="NOTHING_TO_REDO")
        target = stack.pop()
        other = "redo" if direction == "undo" else "undo"
        if current:
            history[other].append(current)
        write_json_atomic(history_path, history)
        result = self._restore_as_new(store, project_id, target, current or "",
                                      source="restore")
        result["session_epoch"] = session.epoch if session else 0
        return result

    def _recipe_for(self, requirement: RequirementSpec):
        if not requirement.template_id:
            return None
        try:
            from mota.catalog.templates import get_template

            return get_template(requirement.template_id)
        except NotFound:
            return None

    def _restore_as_new(self, store: ProjectStore, project_id: str,
                        source_revision_id: str, expected_current: str,
                        *, source: str = "restore") -> dict[str, Any]:
        snapshot = store.load_revision(project_id, source_revision_id)
        draft = store.begin_draft(project_id, expected_current, None)
        draft.requirement = snapshot.requirement
        draft.circuit = snapshot.circuit
        draft.schematic = snapshot.schematic
        draft.board = snapshot.board
        draft.draft_seq += 1
        version = DesignVersion.of_draft(draft.draft_id, draft.draft_seq)
        checks = validate_all(project_id, version, draft.requirement, draft.circuit,
                              draft.schematic, draft.board,
                              self._recipe_for(draft.requirement))
        draft.checks = checks
        store.save_draft(draft)
        meta = store.commit(draft, expected_current, run_id=None,
                            idempotency_key=None,
                            summary=f"Restored from {source_revision_id}",
                            source=source,
                            restored_from_revision_id=source_revision_id)
        new_snapshot = store.load_revision(project_id, meta.revision_id)
        write_revision_scenes(new_snapshot, store)
        return {"commit_status": "COMMITTED", "project_id": project_id,
                "revision_id": meta.revision_id,
                "restored_from_revision_id": source_revision_id,
                "review_status": meta.review_status,
                "checks_summary": meta.checks_summary}

    # ----------------------------------------------------------------------- views
    def cmd_scene_get(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        version = DesignVersion.model_validate(params.get("design_version") or {})
        view = params.get("view", "schematic")
        if view not in ("schematic", "pcb2d", "pcb3d"):
            raise InvalidRequest(f"Unknown view {view}.", code="VIEW_UNKNOWN")
        if version.revision_id:
            snapshot = store.load_revision(project_id, version.revision_id)
            path = store.project_dir(project_id) / "revisions" / version.revision_id / \
                "scene" / f"{view}.json"
            if not path.exists():
                write_revision_scenes(snapshot, store)
            data = read_json(path)
            if data is None:
                raise NotFound("Scene not available for this revision.", code="SCENE_MISSING")
            rows = read_json(store.project_dir(project_id) / "revisions" /
                             version.revision_id / "artifacts.json", default=[]) or []
            artifact = next((r["artifact_id"] for r in rows
                             if r.get("kind") == f"scene_{view}"), None)
            data["artifact_id"] = artifact
            data["read_only"] = version.revision_id != store.current_revision_id(project_id)
            return data
        draft = store.load_draft(project_id, version.draft_id or "")
        if draft.draft_seq != version.draft_seq:
            from mota.errors import DraftConflict

            raise DraftConflict("Stale draft sequence.", data={
                "current_draft_seq": draft.draft_seq,
                "requested_draft_seq": version.draft_seq})
        data = draft_scene_json(store, draft, view)
        data["read_only"] = False
        return data

    def cmd_artifact_read(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        version = DesignVersion.model_validate(params.get("design_version") or {})
        artifact_id = params.get("artifact_id", "")
        offset = int(params.get("offset", 0))
        length = int(params.get("length", 1024 * 1024))
        if offset < 0 or length < 0 or length > 1024 * 1024:
            raise ArtifactInvalid("Invalid byte range.", code="ARTIFACT_INVALID")
        path, entry = find_artifact(store, project_id, version, artifact_id)
        data = path.read_bytes()
        from mota.workspace.atomic import sha256_bytes

        digest = sha256_bytes(data)
        if digest != entry.get("sha256"):
            raise ArtifactInvalid("Artifact hash does not match the manifest.",
                                  code="ARTIFACT_INVALID")
        if offset > len(data):
            raise ArtifactInvalid("Offset is beyond the artifact size.",
                                  code="ARTIFACT_INVALID")
        chunk = data[offset:offset + length]
        return {"artifact_id": artifact_id,
                "design_version": version.to_param(),
                "mime_type": entry.get("mime_type", "application/json"),
                "total_bytes": len(data), "offset": offset,
                "data_base64": base64.b64encode(chunk).decode("ascii"),
                "next_offset": offset + len(chunk) if offset + len(chunk) < len(data) else None,
                "sha256": digest,
                "session_epoch": session.epoch if session else 0}

    def cmd_validation_list(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        version = DesignVersion.model_validate(params.get("design_version") or {})
        if version.revision_id:
            snapshot = store.load_revision(project_id, version.revision_id)
            report = snapshot.checks
        else:
            draft = store.load_draft(project_id, version.draft_id or "")
            if draft.draft_seq != version.draft_seq:
                from mota.errors import DraftConflict

                raise DraftConflict("Stale draft sequence.",
                                    data={"current_draft_seq": draft.draft_seq})
            report = draft.checks or ValidationReport(
                project_id=project_id, design_version=draft.version,
                design_version_key=draft.version.key, review_status="DRAFT")
        return {"report": report.model_dump(), "counts": report.counts,
                "project_id": project_id,
                "design_version": version.to_param()}

    # ------------------------------------------------------------------- revisions
    def cmd_revision_list(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        rows = store.list_revisions(params.get("project_id", ""))
        return {"revisions": [r.model_dump() for r in rows],
                "current_revision_id": store.current_revision_id(params.get("project_id", ""))}

    def cmd_revision_open(self, params: dict[str, Any], session) -> dict[str, Any]:
        store = self._store(self._require(session))
        project_id = params.get("project_id", "")
        revision_id = params.get("revision_id", "")
        self._restore_project_assets(store, project_id)
        snapshot = store.load_revision(project_id, revision_id)
        current = store.current_revision_id(project_id)
        return {"project_id": project_id, "revision": snapshot.revision.model_dump(),
                "requirement": snapshot.requirement.model_dump(),
                "checks": snapshot.checks.model_dump(),
                "read_only": revision_id != current,
                "current_revision_id": current,
                "views": {"available": ["schematic", "pcb2d", "pcb3d"]}}

    def cmd_revision_restore(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        store = self._store(ctx)
        project_id = params.get("project_id", "")
        if store.has_active_run(project_id):
            raise ProjectBusy("Finish or cancel the current build first.",
                              data={"project_id": project_id})
        expected = params.get("expected_revision_id") or store.current_revision_id(project_id)
        result = self._restore_as_new(store, project_id,
                                      params.get("source_revision_id", ""),
                                      expected or "", source="restore")
        result["session_epoch"] = session.epoch if session else 0
        return result

    # ---------------------------------------------------------------------- memory
    def cmd_memory_list(self, params: dict[str, Any], session) -> dict[str, Any]:
        ctx = self._require(session)
        memory = self._memory(ctx)
        layer = params.get("layer", "long_term")
        entries = memory.list(layer, params.get("project_id"),
                              params.get("session_id"))
        return {"layer": layer, "entries": [e.model_dump() for e in entries],
                "tombstones": memory.tombstones()}

    def cmd_memory_read(self, params: dict[str, Any], session) -> dict[str, Any]:
        memory = self._memory(self._require(session))
        entry = memory.read(params.get("entry_id", ""))
        return {"entry": entry.model_dump()}

    def cmd_memory_update(self, params: dict[str, Any], session) -> dict[str, Any]:
        memory = self._memory(self._require(session))
        entry = memory.update(params.get("entry_id", ""), params.get("content", ""),
                              params.get("expected_hash", ""),
                              scope=params.get("scope"),
                              project_id=params.get("project_id"))
        return {"entry": entry.model_dump()}

    def cmd_memory_forget(self, params: dict[str, Any], session) -> dict[str, Any]:
        memory = self._memory(self._require(session))
        return memory.forget(params.get("entry_id", ""),
                             semantic_key=params.get("semantic_key"))
