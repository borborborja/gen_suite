"""Offline bootstrap checks. Run with unittest, independently of database fixtures."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.bootstrap_admin import BootstrapError, create_initial_admin


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    def session(self, results):
        session = Mock()
        session.scalar = AsyncMock(side_effect=results)
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        return session

    async def test_creates_first_admin_under_shared_registration_lock(self):
        session = self.session([None, None])
        with patch("app.bootstrap_admin.hash_password", return_value="hashed"):
            self.assertTrue(await create_initial_admin(session, "Operator@Example.org", "long-password-123"))
        self.assertIn("first_user_admin", str(session.execute.call_args.args[0]))
        user = session.add.call_args.args[0]
        self.assertEqual(user.email, "operator@example.org")
        self.assertEqual(user.password_hash, "hashed")
        self.assertTrue(user.is_server_admin)
        self.assertIsNotNone(user.email_verified_at)
        session.flush.assert_awaited_once()

    async def test_existing_admin_is_not_modified(self):
        session = self.session(["existing-id"])
        with patch("app.bootstrap_admin.hash_password", return_value="hashed"):
            self.assertFalse(await create_initial_admin(session, "other@example.org", "long-password-123"))
        session.add.assert_not_called()
        session.flush.assert_not_awaited()

    async def test_never_promotes_or_overwrites_an_existing_user(self):
        session = self.session([None, "ordinary-user-id"])
        with patch("app.bootstrap_admin.hash_password", return_value="hashed"):
            with self.assertRaises(BootstrapError):
                await create_initial_admin(session, "user@example.org", "long-password-123")
        session.add.assert_not_called()

    async def test_invalid_password_cannot_start_database_mutation(self):
        session = self.session([])
        with self.assertRaises(BootstrapError):
            await create_initial_admin(session, "user@example.org", "short")
        session.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
