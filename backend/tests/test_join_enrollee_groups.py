"""Group-scoped enrollee lists and auto-approve toggles.

These need a real database (the models use a Postgres partial unique index),
so they run against PORTAL_TEST_DATABASE_URL when it is set and skip
otherwise. `docker compose exec backend python -m unittest` has it via the
compose network; a bare checkout simply skips them.
"""

import asyncio
import os
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.app_setting import AppSetting  # noqa: F401 - registers the table
from app.models.join_enrollee import JoinEnrollee  # noqa: F401 - registers the table
from app.services.join_enrollee_service import (
    EnrolleeError,
    auto_approve_settings,
    create_enrollee,
    is_auto_approve_enabled,
    list_enrollees,
    list_join_picker_names,
    save_auto_approve,
    set_enrollee_banned,
)
from app.services.join_group import GROUP_SB, GROUP_VCS

TEST_DB_URL = os.getenv("PORTAL_TEST_DATABASE_URL", "")


def run(coro):
    return asyncio.run(coro)


async def _fresh_factory():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        for table in ("join_enrollees", "app_settings"):
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[JoinEnrollee.__table__, AppSetting.__table__],
        )
    return async_sessionmaker(engine, expire_on_commit=False)


@unittest.skipUnless(TEST_DB_URL, "PORTAL_TEST_DATABASE_URL is not set")
class EnrolleeGroupTests(unittest.TestCase):
    def _with_session(self, body):
        async def main():
            factory = await _fresh_factory()
            async with factory() as session:
                # Skip the legacy discovery pass; these tests seed explicitly.
                session.add(AppSetting(key="join_enrollees_seeded", value="1"))
                await session.commit()
                return await body(session)

        return run(main())

    def test_default_group_is_sb(self):
        async def body(session: AsyncSession):
            row = await create_enrollee(session, "Gaurav")
            return row.group_tag

        self.assertEqual(self._with_session(body), GROUP_SB)

    def test_dropdown_only_offers_that_group(self):
        async def body(session: AsyncSession):
            await create_enrollee(session, "Gaurav", GROUP_SB)
            await create_enrollee(session, "Snig", GROUP_SB)
            await create_enrollee(session, "Priya", GROUP_VCS)
            return (
                await list_join_picker_names(session, GROUP_SB),
                await list_join_picker_names(session, GROUP_VCS),
            )

        sb, vcs = self._with_session(body)
        self.assertEqual(sb, ["Gaurav", "Snig"])
        self.assertEqual(vcs, ["Priya"])

    def test_same_name_may_exist_in_both_groups(self):
        async def body(session: AsyncSession):
            await create_enrollee(session, "Gaurav", GROUP_SB)
            await create_enrollee(session, "Gaurav", GROUP_VCS)
            return [(row.name, row.group_tag) for row in await list_enrollees(session)]

        rows = self._with_session(body)
        self.assertEqual(sorted(rows), [("Gaurav", GROUP_SB), ("Gaurav", GROUP_VCS)])

    def test_duplicate_within_one_group_is_rejected(self):
        async def body(session: AsyncSession):
            await create_enrollee(session, "Gaurav", GROUP_VCS)
            try:
                await create_enrollee(session, "gaurav", GROUP_VCS)
            except EnrolleeError as exc:
                return str(exc)
            return None

        self.assertIn("VCS list", self._with_session(body) or "")

    def test_ban_only_removes_that_groups_entry(self):
        async def body(session: AsyncSession):
            sb = await create_enrollee(session, "Gaurav", GROUP_SB)
            await create_enrollee(session, "Gaurav", GROUP_VCS)
            await set_enrollee_banned(session, sb.id, True)
            return (
                await list_join_picker_names(session, GROUP_SB),
                await list_join_picker_names(session, GROUP_VCS),
            )

        sb, vcs = self._with_session(body)
        self.assertEqual(sb, [])
        self.assertEqual(vcs, ["Gaurav"])

    def test_listing_can_be_filtered_by_group(self):
        async def body(session: AsyncSession):
            await create_enrollee(session, "Gaurav", GROUP_SB)
            await create_enrollee(session, "Priya", GROUP_VCS)
            return [row.name for row in await list_enrollees(session, GROUP_VCS)]

        self.assertEqual(self._with_session(body), ["Priya"])


@unittest.skipUnless(TEST_DB_URL, "PORTAL_TEST_DATABASE_URL is not set")
class AutoApproveGroupTests(unittest.TestCase):
    def test_toggles_are_independent(self):
        async def main():
            factory = await _fresh_factory()
            async with factory() as session:
                self.assertEqual(
                    await auto_approve_settings(session), {GROUP_SB: False, GROUP_VCS: False}
                )
                await save_auto_approve(session, True, GROUP_VCS)
                self.assertFalse(await is_auto_approve_enabled(session, GROUP_SB))
                self.assertTrue(await is_auto_approve_enabled(session, GROUP_VCS))
                await save_auto_approve(session, True, GROUP_SB)
                await save_auto_approve(session, False, GROUP_VCS)
                self.assertTrue(await is_auto_approve_enabled(session, GROUP_SB))
                self.assertFalse(await is_auto_approve_enabled(session, GROUP_VCS))

        run(main())

    def test_unknown_group_reads_as_sb(self):
        async def main():
            factory = await _fresh_factory()
            async with factory() as session:
                await save_auto_approve(session, True, "sb")
                self.assertTrue(await is_auto_approve_enabled(session, "nonsense"))
                self.assertTrue(await is_auto_approve_enabled(session, None))

        run(main())


if __name__ == "__main__":
    unittest.main()
