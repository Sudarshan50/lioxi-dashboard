"""Portal account names must be allocated under a lock.

Two concurrent commits that read the same taken-set allocate the same name.
The portal later renames one of them, but its Azure stack was already built
from the original, so the two disagree permanently.
"""

import asyncio
import unittest
from sqlalchemy.exc import IntegrityError

from app.services.submit_service import (
    NAME_ALLOCATION_LOCK,
    _is_duplicate_name_error,
    allocate_submit_name,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Records statements; returns account names then submission names."""

    def __init__(self, account_names=(), submit_names=()):
        self.statements = []
        self._queued = [
            [(n,) for n in account_names],
            [(n,) for n in submit_names],
        ]

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        if "pg_advisory_xact_lock" in str(statement):
            return _FakeResult([])
        return _FakeResult(self._queued.pop(0) if self._queued else [])


class AllocationLockTests(unittest.TestCase):
    def test_lock_is_taken_before_any_name_is_read(self):
        db = _FakeSession()
        _run(allocate_submit_name(db, "Lioxi-Ayush", exclude_id=1))
        first = db.statements[0][0]
        self.assertIn("pg_advisory_xact_lock", first)

    def test_lock_uses_the_shared_key(self):
        db = _FakeSession()
        _run(allocate_submit_name(db, "Lioxi-Ayush", exclude_id=1))
        self.assertEqual(db.statements[0][1], {"key": NAME_ALLOCATION_LOCK})

    def test_free_name_is_returned_unchanged(self):
        db = _FakeSession(account_names=["Other"], submit_names=[])
        self.assertEqual(_run(allocate_submit_name(db, "Lioxi-Ayush", 1)), "Lioxi-Ayush")

    def test_collides_against_accounts_and_submissions_alike(self):
        db = _FakeSession(account_names=["Lioxi-Ayush"], submit_names=["Lioxi-Ayush1"])
        self.assertEqual(_run(allocate_submit_name(db, "Lioxi-Ayush", 1)), "Lioxi-Ayush2")

    def test_matching_is_case_insensitive(self):
        db = _FakeSession(account_names=["lioxi-ayush"], submit_names=[])
        self.assertEqual(_run(allocate_submit_name(db, "Lioxi-Ayush", 1)), "Lioxi-Ayush1")

    def test_vcs_names_collide_the_same_way(self):
        db = _FakeSession(account_names=["Ambarish"], submit_names=[])
        self.assertEqual(_run(allocate_submit_name(db, "Ambarish", 1)), "Ambarish1")


class DuplicateNameErrorTests(unittest.TestCase):
    """The backstop index and the live-subscription index must not be confused."""

    def _error(self, message):
        # orig mirrors what asyncpg raises: the constraint name is in the text.
        return IntegrityError("stmt", {}, Exception(message))

    def test_name_constraint_is_recognised(self):
        exc = self._error('duplicate key value violates unique constraint "uq_sp_submit_name"')
        self.assertTrue(_is_duplicate_name_error(exc))

    def test_subscription_constraint_is_not_a_name_clash(self):
        exc = self._error(
            'duplicate key value violates unique constraint "uq_sp_submit_live_subscription"'
        )
        self.assertFalse(_is_duplicate_name_error(exc))


if __name__ == "__main__":
    unittest.main()
