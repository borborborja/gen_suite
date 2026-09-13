"""Offline index checkpoints and revision safety; no provider calls or real storage."""
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.tasks.index_tasks import persist_index_page


class IndexResumeTests(unittest.IsolatedAsyncioTestCase):
    def arguments(self):
        return dict(tenant_id=uuid.uuid4(), document_id=uuid.uuid4(), transcription_id=uuid.uuid4(),
                    page_id=uuid.uuid4(), job_id=uuid.uuid4(), usage={"ai_call_id": "fixture-call"})

    async def test_invalid_or_stale_response_cannot_clear_previous_entries(self):
        for raw, active in (({}, True), ({"entries": []}, True), ({"entries": [{"name_raw": "Ana"}]}, False)):
            session = MagicMock(scalar=AsyncMock(side_effect=[uuid.uuid4(), active]), execute=AsyncMock())
            with self.subTest(raw=raw, active=active), self.assertRaises(ValueError):
                await persist_index_page(session, raw=raw, **self.arguments())
            session.execute.assert_not_awaited(); session.add.assert_not_called()

    async def test_replayed_checkpoint_does_not_delete_or_duplicate_entries(self):
        session = MagicMock(scalar=AsyncMock(side_effect=[uuid.uuid4(), True, object()]), execute=AsyncMock())
        self.assertEqual(await persist_index_page(session, raw={"entries": [{"name_raw": "Ana"}]}, **self.arguments()), 0)
        session.execute.assert_not_awaited(); session.add.assert_not_called()

    async def test_validated_page_and_empty_page_checkpoint_atomically(self):
        for raw, count in (({"entries": [{"name_raw": "Ana López", "folio_label": "12v"}]}, 1),
                           ({"entries": [], "has_index": False}, 0)):
            session = MagicMock(scalar=AsyncMock(side_effect=[uuid.uuid4(), True, None]), execute=AsyncMock())
            args = self.arguments()
            self.assertEqual(await persist_index_page(session, raw=raw, **args), count)
            statements = [call.args[0] for call in session.execute.call_args_list]
            self.assertEqual(len(statements), 2)
            self.assertIn("index_entries.page_id", str(statements[0]))
            self.assertEqual(statements[1].compile().params["stage"], "index")
            if count:
                row = session.add.call_args.args[0]
                self.assertEqual(row.raw_json["_transcription_id"], str(args["transcription_id"]))
                self.assertEqual(row.raw_json["_ai_call_id"], "fixture-call")
            session.commit.assert_not_called()
