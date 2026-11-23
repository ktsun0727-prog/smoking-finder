"""보관함(cloudsync) 설정이 없을 때 아무것도 하지 않는지 확인."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cloudsync  # noqa: E402


class CloudSyncOff(unittest.TestCase):
    def setUp(self):
        self._r, self._t = cloudsync.REPO, cloudsync.TOKEN
        cloudsync.REPO = cloudsync.TOKEN = ""

    def tearDown(self):
        cloudsync.REPO, cloudsync.TOKEN = self._r, self._t

    def test_disabled_is_noop(self):
        d = tempfile.mkdtemp()
        db = os.path.join(d, "app.db")
        self.assertFalse(cloudsync.enabled())
        self.assertFalse(cloudsync.restore(db, os.path.join(d, "photos")))
        self.assertFalse(os.path.exists(db))
        cloudsync.mark_dirty()
        self.assertFalse(cloudsync._dirty.is_set())
        self.assertFalse(cloudsync.push_db(db))


if __name__ == "__main__":
    unittest.main()
