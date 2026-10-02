"""Create or refresh the TalentLens database with demo accounts, jobs and the sample cohort.

    python seed.py              # create talentlens.db (safe to run again — it is idempotent)
    python seed.py --reset      # delete the database first and start clean
    python seed.py --stats      # just print what is in the database

Demo sign-ins (change these before any real deployment):
    admin      admin@talentlens.dev      Admin@2026
    recruiter  recruiter@talentlens.dev  Recruit@2026
    candidate  candidate@talentlens.dev  Candidate@2026   (linked to Nisha Menon's resume)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import services as S
from app.db import DEFAULT_DB, Database
from app.evaluation import evaluate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    path = Path(args.db)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    db = Database(path)

    if not args.stats:
        out = S.seed_demo(db, Path(__file__).parent / "sample_resumes")
        print(f"seeded: {out['accepted']} of {out['resumes']} resumes accepted, "
              f"accounts created: {out['accounts_created'] or 'already present'}")

    candidates = [db.profile(c["candidate_id"]) for c in db.list_candidates()]
    from app.evaluation import EVAL_JOBS
    acc = evaluate(candidates, EVAL_JOBS) if candidates else {}
    rec = db.get_user(db.user_with_hash("recruiter@talentlens.dev")["user_id"])
    print(json.dumps({
        "database": str(path.resolve()),
        "tables": db.stats()["tables"],
        "milestone_1_avg_field_recall": round(sum(c["field_recall"] for c in db.list_candidates())
                                              / max(1, len(candidates)), 1),
        "milestone_2_matching_accuracy": acc.get("accuracy"),
        "dashboard": S.dashboard(db, rec)["kpis"],
        "sign_in": {email: password for email, _, password, _ in S.DEMO_ACCOUNTS},
    }, indent=2))


if __name__ == "__main__":
    main()
