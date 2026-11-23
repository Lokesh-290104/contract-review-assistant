"""Background worker for reviews.

A small thread pool (2 workers by default) runs reviews so uploads return at once and
large files never block the web server. Two workers also keep us under the free
models' per-minute limits. On start-up, reviews cut off by a restart are marked failed
so the user can press Retry (see `recover_interrupted`).
"""

import uuid
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import update

from .config import get_settings
from .db import new_session
from .models import Contract, ContractStatus

_pool: ThreadPoolExecutor | None = None
_inline = False  # tests run reviews synchronously


def set_inline(value: bool) -> None:
    global _inline
    _inline = value


def submit_review(contract_id: uuid.UUID, user_id: str) -> Future | None:
    from .review import run_review

    if _inline:
        run_review(contract_id, user_id)
        return None
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=get_settings().review_workers, thread_name_prefix="review")
    return _pool.submit(run_review, contract_id, user_id)


def recover_interrupted() -> int:
    with new_session() as db:
        result = db.execute(
            update(Contract)
            .where(Contract.status == ContractStatus.PROCESSING)
            .values(status=ContractStatus.FAILED, error="The review was interrupted by a server restart. Press Retry.")
        )
        db.commit()
        return result.rowcount or 0


def shutdown() -> None:
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
