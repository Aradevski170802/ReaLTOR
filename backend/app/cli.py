"""Command-line operations. Run `python -m app.cli --help` from backend/."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import init_engine, session_scope


def _migrate(_args) -> None:
    from app.main import run_migrations

    run_migrations()
    print("Database migrated to head")


def _create_user(args) -> None:
    from app.api.deps import hash_token, new_token
    from app.models import User

    token = new_token()
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == args.email))
        if user is None:
            user = User(email=args.email, display_name=args.name or args.email, role=args.role)
            s.add(user)
        user.role = args.role
        user.token_hash = hash_token(token)
        user.is_active = True
    print(f"User {args.email} ({args.role}). API token (shown once): {token}")


def _seed(args) -> None:
    from app.seed import seed_demo

    print(json.dumps(seed_demo(with_enrichment=not args.no_enrichment), indent=2, default=str))


def _import_file(args) -> None:
    from app.models import Project
    from app.services.imports import commit_import, run_parse, save_upload

    with session_scope() as s:
        project = s.get(Project, args.project_id)
        if project is None:
            sys.exit(f"Project {args.project_id} not found")
        path = Path(args.file)
        sale_list = save_upload(s, project, path.name, path.read_bytes(), "cli")
        result = run_parse(s, sale_list, "cli")
        print(f"Import {sale_list.id}: {len(result.records)} records, {result.stats['needs_review']} need review, warnings={result.warnings[:5]}")
        if args.commit:
            outcome = commit_import(s, sale_list, "cli", confirm_overwrite=args.confirm_overwrite)
            print(f"Committed: {outcome.__dict__}")


def _create_project(args) -> None:
    from app.models import Project

    with session_scope() as s:
        project = Project(name=args.name, county=args.county, is_demo=args.demo, created_by="cli")
        s.add(project)
        s.flush()
        print(f"Project {project.id}: {project.name} ({project.county})")


def _pilot(args) -> None:
    from sqlalchemy import update

    from app.models import Property

    with session_scope() as s:
        s.execute(update(Property).where(Property.project_id == args.project_id).values(is_pilot=False))
        ids = list(s.scalars(select(Property.id).where(Property.project_id == args.project_id, Property.excluded.is_(False))
                             .order_by(Property.sequence, Property.id).limit(args.count)))
        s.execute(update(Property).where(Property.id.in_(ids)).values(is_pilot=True))
    print(f"Pilot properties: {ids}")


def _run_worker_now() -> None:
    from app.jobs.worker import Worker

    processed = Worker(enable_scheduler=False).run_until_empty()
    print(f"Processed {processed} job(s)")


def _enrich(args) -> None:
    from app.jobs.queue import enqueue

    with session_scope() as s:
        job = enqueue(s, "enrich_batch", {"selection": args.selection, "sources": args.sources, "force": args.force},
                      project_id=args.project_id, created_by="cli", max_attempts=1)
        print(f"Queued enrichment batch job {job.id}")
    if args.sync:
        _run_worker_now()


def _refresh(args) -> None:
    from app.jobs.queue import enqueue
    from app.jobs.scheduler import enqueue_daily_refresh

    if args.all:
        print(f"Queued refresh jobs: {enqueue_daily_refresh('manual')}")
    else:
        with session_scope() as s:
            job = enqueue(s, "refresh_tax_status", {"trigger": "manual"}, project_id=args.project_id, created_by="cli")
            print(f"Queued refresh job {job.id}")
    if args.sync:
        _run_worker_now()


def _export(args) -> None:
    from app.export.xlsx_exporter import export_project
    from app.models import Project

    with session_scope() as s:
        record = export_project(s, s.get(Project, args.project_id), "cli")
        print(record.file_path)


def _ack(args) -> None:
    from app.services.policies import acknowledge_terms

    with session_scope() as s:
        acknowledge_terms(s, args.source, args.actor, args.note)
    print(f"Terms acknowledged for {args.source}")


def _set_secret(args) -> None:
    from app.api.routers.settings import ALLOWED_SECRETS
    from app.security.secrets import set_secret

    if args.name not in ALLOWED_SECRETS:
        sys.exit(f"Unknown secret. Allowed: {sorted(ALLOWED_SECRETS)}")
    value = getpass.getpass(f"Value for {args.name} (input hidden): ")
    with session_scope() as s:
        set_secret(s, args.name, value.strip(), "cli")
    print("Saved (encrypted).")


def _verify(_args) -> None:
    from app.audit import verify_chain

    with session_scope() as s:
        ok, bad = verify_chain(s)
    print("Audit chain intact" if ok else f"Audit chain broken at entry {bad}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate").set_defaults(fn=_migrate)
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--name")
    p.add_argument("--role", choices=["viewer", "analyst", "admin"], default="analyst")
    p.set_defaults(fn=_create_user)
    p = sub.add_parser("seed-demo")
    p.add_argument("--no-enrichment", action="store_true")
    p.set_defaults(fn=_seed)
    p = sub.add_parser("create-project")
    p.add_argument("--name", required=True)
    p.add_argument("--county", choices=["montco", "delco"], required=True)
    p.add_argument("--demo", action="store_true")
    p.set_defaults(fn=_create_project)
    p = sub.add_parser("import")
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--confirm-overwrite", action="store_true")
    p.set_defaults(fn=_import_file)
    p = sub.add_parser("pilot")
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--count", type=int, default=8)
    p.set_defaults(fn=_pilot)
    p = sub.add_parser("enrich")
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--selection", choices=["pilot", "all"], default="pilot")
    p.add_argument("--sources", nargs="*")
    p.add_argument("--force", action="store_true")
    p.add_argument("--sync", action="store_true", help="process the queue in this process")
    p.set_defaults(fn=_enrich)
    p = sub.add_parser("refresh-tax-status")
    p.add_argument("--project-id", type=int)
    p.add_argument("--all", action="store_true")
    p.add_argument("--sync", action="store_true")
    p.set_defaults(fn=_refresh)
    p = sub.add_parser("export")
    p.add_argument("--project-id", type=int, required=True)
    p.set_defaults(fn=_export)
    p = sub.add_parser("acknowledge-terms")
    p.add_argument("--source", required=True)
    p.add_argument("--actor", default="cli-admin")
    p.add_argument("--note", default=None)
    p.set_defaults(fn=_ack)
    p = sub.add_parser("set-secret")
    p.add_argument("--name", required=True)
    p.set_defaults(fn=_set_secret)
    sub.add_parser("verify-audit").set_defaults(fn=_verify)
    sub.add_parser("run-queue").set_defaults(fn=lambda _a: _run_worker_now())
    args = parser.parse_args()
    get_settings().ensure_dirs()
    init_engine()
    args.fn(args)


if __name__ == "__main__":
    main()
