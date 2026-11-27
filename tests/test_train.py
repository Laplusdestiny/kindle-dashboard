"""train.view の表示本数と「間に合わない電車を外す」処理のテスト。

依存を増やさないため標準の unittest で書いている。実行: uv run python -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from kindle_dash_server import train

# 2026-11-27（金）は平日。祝日CSVは置かないので、土日だけで判定される
NOW = datetime(2026, 11, 27, 18, 0)


def every_5_minutes() -> list[dict]:
    """5:00〜24:55 まで5分おきに発車する時刻表"""
    return [{"t": m, "type": "普通", "dest": "大阪梅田"} for m in range(5 * 60, 25 * 60, 5)]


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env(DATA_DIR=self.tmp.name)
        for day in ("w", "h"):
            url, path = train._timetable_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "trains": every_5_minutes()}), encoding="utf-8")

    def env(self, **values: str) -> None:
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_are_limited_to_8_by_default(self):
        self.env(TRAIN_WINDOW_MINUTES="60")  # 60分以内なら13本ある
        rows = train.view(NOW)["rows"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["time"], "18:00")
        self.assertEqual(rows[-1]["time"], "18:35")

    def test_max_rows_can_be_changed(self):
        self.env(TRAIN_WINDOW_MINUTES="60", TRAIN_MAX_ROWS="3")
        self.assertEqual([r["time"] for r in train.view(NOW)["rows"]], ["18:00", "18:05", "18:10"])

    def test_max_rows_0_means_no_limit(self):
        self.env(TRAIN_WINDOW_MINUTES="60", TRAIN_MAX_ROWS="0")
        self.assertEqual(len(train.view(NOW)["rows"]), 13)

    def test_trains_you_cannot_catch_are_hidden(self):
        # 駅まで7分 → 18:07 より前に出る電車は間に合わない
        self.env(TRAIN_WINDOW_MINUTES="30", TRAIN_WALK_MINUTES="7")
        out = train.view(NOW)
        self.assertEqual([r["time"] for r in out["rows"]], ["18:10", "18:15", "18:20", "18:25", "18:30"])
        self.assertEqual(out["rows"][0]["wait"], 10)  # 待ち時間は今からの分数のまま
        self.assertEqual(out["walk"], 7)

    def test_train_leaving_exactly_when_you_arrive_is_shown(self):
        self.env(TRAIN_WINDOW_MINUTES="30", TRAIN_WALK_MINUTES="5")
        self.assertEqual(train.view(NOW)["rows"][0]["time"], "18:05")

    def test_walk_defaults_to_0(self):
        self.env(TRAIN_WINDOW_MINUTES="30")
        out = train.view(NOW)
        self.assertEqual(out["rows"][0]["time"], "18:00")
        self.assertEqual(out["walk"], 0)

    def test_next_skips_trains_you_cannot_catch(self):
        # 窓が徒歩時間より短いと表には何も出ない。「次は」も間に合う電車にする
        self.env(TRAIN_WINDOW_MINUTES="5", TRAIN_WALK_MINUTES="12")
        out = train.view(NOW)
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["next"], "18:15")


if __name__ == "__main__":
    unittest.main()
