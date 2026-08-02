"""Operator CLI for the channels/agenda/session layer.

Usage:
    python -m backend.cli agenda --channel 3 --date 2026-08-02
    python -m backend.cli agenda --channel 3 --date 2026-08-02 --generate
    python -m backend.cli refresh --channel 3
    python -m backend.cli refresh --channel 3 --job 4821
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_


def _parse_date(value: str | None) -> date_:
    from datetime import datetime

    if not value:
        return datetime.utcnow().date()
    return date_.fromisoformat(value)


def cmd_agenda(args: argparse.Namespace) -> int:
    from backend.agents.agenda import generate_daily_agenda, get_or_create_session
    from backend.database import SessionLocal
    from backend.models import Channel, VideoJob

    db = SessionLocal()
    try:
        channel = db.get(Channel, args.channel)
        if channel is None:
            print(f"canal {args.channel} não encontrado", file=sys.stderr)
            return 1
        target_date = _parse_date(args.date)
        if args.generate:
            session = generate_daily_agenda(db, channel, target_date)
        else:
            session = get_or_create_session(db, channel, target_date)
            db.commit()

        jobs = []
        if session.planned_items:
            rows = db.query(VideoJob).filter(VideoJob.id.in_(session.planned_items)).all()
            by_id = {j.id: j for j in rows}
            for jid in session.planned_items:
                j = by_id.get(jid)
                jobs.append({
                    "job_id": jid,
                    "title": j.title if j else None,
                    "video_format": j.video_format if j else None,
                    "scheduled_at": j.scheduled_at.isoformat() if j and j.scheduled_at else None,
                    "status": j.status.value if j else "missing",
                })
        print(json.dumps({"session": session.to_dict(), "jobs": jobs}, indent=2, ensure_ascii=False))
        return 0
    finally:
        db.close()


def cmd_refresh(args: argparse.Namespace) -> int:
    from backend.agents.channel_refresh import refresh_channel, refresh_job
    from backend.database import SessionLocal
    from backend.models import Channel, VideoJob

    db = SessionLocal()
    try:
        channel = db.get(Channel, args.channel)
        if channel is None:
            print(f"canal {args.channel} não encontrado", file=sys.stderr)
            return 1
        if args.job:
            job = db.get(VideoJob, args.job)
            if job is None or job.account_id != channel.account_id:
                print(f"job {args.job} não encontrado neste canal", file=sys.stderr)
                return 1
            results = [refresh_job(db, channel, job)]
        else:
            results = refresh_channel(db, channel)
        print(json.dumps({"results": results}, indent=2, ensure_ascii=False))
        return 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_agenda = sub.add_parser("agenda", help="Ver/gerar a agenda do dia de um canal")
    p_agenda.add_argument("--channel", type=int, required=True)
    p_agenda.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (default: hoje)")
    p_agenda.add_argument("--generate", action="store_true",
                           help="Gera jobs pra fechar os limites diarios do canal (sincrono, pode demorar)")
    p_agenda.set_defaults(func=cmd_agenda)

    p_refresh = sub.add_parser("refresh", help="Reprocessa curadoria/design de um canal ou item")
    p_refresh.add_argument("--channel", type=int, required=True)
    p_refresh.add_argument("--job", type=int, default=None, help="Limita a um job especifico")
    p_refresh.set_defaults(func=cmd_refresh)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
