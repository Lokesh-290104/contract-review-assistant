"""Evaluation: run the real review pipeline on the sample contracts and score it.

    .venv\\Scripts\\python eval\\run_eval.py      (needs OPENROUTER_API_KEY in .env)

An item counts as correct when the status matches the expected status AND, for
"found" items, the verified quote contains the expected phrase. It uses a
throw-away embedded Postgres, so your real data is never touched.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    os.chdir(ROOT / "backend")  # so settings find ../.env

    import pgserver

    from app import db as app_db
    from app.checklist import CHECKLIST
    from app.llm import AIError, get_ai
    from app.models import Contract, ContractFile
    from app.review import review_contract

    expected = json.loads((ROOT / "eval" / "expected.json").read_text(encoding="utf-8"))
    tmp = tempfile.mkdtemp(prefix="cra-eval-")
    server = pgserver.get_server(tmp, cleanup_mode="stop")
    try:
        server.psql("CREATE DATABASE cra_eval;")
        app_db.init_engine(server.get_uri("cra_eval").replace("postgresql://", "postgresql+psycopg://", 1))
        app_db.create_schema()
        ai = get_ai()

        total = correct = 0
        report = {}
        for name, items in expected.items():
            data = (ROOT / "eval" / "samples" / name).read_bytes()
            with app_db.new_session() as db:
                contract = Contract(user_id="eval", filename=name, content_type="txt", size_bytes=len(data))
                db.add(contract)
                db.flush()
                db.add(ContractFile(contract_id=contract.id, data=data))
                db.commit()
                try:
                    findings = {f.item_key: f for f in review_contract(db, contract, ai)}
                except AIError as exc:
                    print(f"{name}: AI error: {exc}")
                    return 2
                db.commit()

            print(f"\n{name}")
            report[name] = {}
            for item in CHECKLIST:
                want = items[item.key]
                got = findings[item.key]
                ok = got.status == want["status"]
                if ok and want["status"] == "found":
                    ok = bool(got.quote) and want["quote_contains"].lower() in got.quote.lower()
                total += 1
                correct += ok
                mark = "PASS" if ok else "FAIL"
                print(f"  {mark}  {item.label:<26} expected {want['status']:<9} got {got.status:<9} {(got.quote or '')[:70]!r}")
                if got.note:
                    print(f"        note: {got.note}")
                report[name][item.key] = {"ok": ok, "expected": want, "status": got.status, "quote": got.quote, "summary": got.summary, "note": got.note}

        print(f"\nScore: {correct}/{total} items correct ({100 * correct / total:.0f}%)")
        out = ROOT / "eval" / "results.json"
        out.write_text(json.dumps({"score": f"{correct}/{total}", "items": report}, indent=2), encoding="utf-8")
        print(f"Details saved to {out.relative_to(ROOT)}")
        return 0
    finally:
        server.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
