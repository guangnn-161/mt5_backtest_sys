import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ftmo_bot'))
from tools import download_mt5_data as downloader
from tools.market_data_store import MarketDataStore, storage_key


class FakeStore:
    def __init__(self, latest=None):
        self.latest = latest
        self.writes = []
        self.states = []

    def latest_timestamp(self, symbol, timeframe):
        return self.latest

    def write_bars(self, symbol, timeframe, frame):
        self.writes.append((symbol, timeframe, frame.copy()))
        return len(frame)

    def mark_state(self, symbol, timeframe, status, **kwargs):
        self.states.append((symbol, timeframe, status))


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self):
        self.selected = []
        self.requests = []

    def symbols_get(self):
        return [SimpleNamespace(name='XAUUSD', visible=True),
                SimpleNamespace(name='HIDDEN', visible=False)]

    def symbol_select(self, symbol, enabled):
        self.selected.append((symbol, enabled))
        return True

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.requests.append((symbol, timeframe, start, end))
        return [{'time': int(start.timestamp()), 'open': 1., 'high': 1., 'low': 1.,
                 'close': 1., 'tick_volume': 1, 'spread': 0, 'real_volume': 0}]

    def last_error(self):
        return (0, 'ok')


class MT5SyncTests(unittest.TestCase):
    def test_all_standard_timeframes_are_selected(self):
        self.assertEqual(downloader.requested_timeframes({'timeframes': 'all'}),
                         list(downloader.TIMEFRAMES))
        with self.assertRaises(ValueError):
            downloader.requested_timeframes({'timeframes': ['M5', 'BAD']})
        with self.assertRaises(ValueError):
            downloader.requested_timeframes({'timeframes': 'M5'})

    def test_market_watch_excludes_hidden_symbols(self):
        with patch.object(downloader, 'mt5', FakeMT5()):
            self.assertEqual(downloader.market_watch_symbols(), ['XAUUSD'])

    def test_sync_pair_uses_incremental_overlap_and_records_bars(self):
        fake_mt5 = FakeMT5()
        latest = pd.Timestamp('2025-01-10T00:00:00Z')
        store = FakeStore(latest)
        with patch.object(downloader, 'mt5', fake_mt5), \
             patch.dict(downloader.TIMEFRAMES, {'M1': 31}, clear=True):
            result = downloader.sync_pair(
                store, 'XAUUSD', 'M1',
                start=datetime(2010, 1, 1, tzinfo=timezone.utc),
                end=datetime(2025, 1, 11, tzinfo=timezone.utc), overlap_days=3,
            )
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['bars_received'], 1)
        self.assertEqual(fake_mt5.selected, [('XAUUSD', True)])
        self.assertEqual(fake_mt5.requests[0][2], datetime(2025, 1, 7, tzinfo=timezone.utc))
        self.assertEqual(store.writes[0][1], 'M1')

    def test_empty_incremental_overlap_keeps_existing_coverage(self):
        fake_mt5 = FakeMT5()
        fake_mt5.copy_rates_range = lambda *args: []
        store = FakeStore(pd.Timestamp('2025-01-10T00:00:00Z'))
        with patch.object(downloader, 'mt5', fake_mt5), \
             patch.dict(downloader.TIMEFRAMES, {'M1': 31}, clear=True):
            result = downloader.sync_pair(
                store, 'XAUUSD', 'M1',
                start=datetime(2010, 1, 1, tzinfo=timezone.utc),
                end=datetime(2025, 1, 11, tzinfo=timezone.utc), overlap_days=3,
            )
        self.assertEqual(result['status'], 'up_to_date')
        self.assertEqual(store.states, [])

    def test_catalog_tracks_resumable_pair_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MarketDataStore(Path(temp))
            try:
                store.mark_state('EUR/USD', 'M5', 'no_data')
                row = store.connection.execute(
                    'SELECT storage_key, status FROM sync_state WHERE symbol=? AND timeframe=?',
                    ('EUR/USD', 'M5'),
                ).fetchone()
                self.assertEqual(row['storage_key'], storage_key('EUR/USD'))
                self.assertEqual(row['status'], 'no_data')
                store.start_run('run_test', {'timeframes': 'all'})
                store.finish_run('run_test', 'completed', {'ok': 1})
                self.assertTrue((Path(temp) / 'manifests' / 'run_test.json').is_file())
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
