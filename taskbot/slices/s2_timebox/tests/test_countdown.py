"""FR-20 / AC3: the countdown is correct to within one second of server time."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from taskbot.core import clock
from tests.fixtures import seed_item

STATIC = Path(__file__).resolve().parents[1] / "static"
NOW = datetime(2026, 11, 20, 9, 0, 0, 250_000, tzinfo=timezone.utc)  # sub-second on purpose


def test_list_returns_server_now_with_ms_and_exact_remaining(client):
    clock.set_now(NOW)
    soon = seed_item(status="timeboxed", title="soon", timebox_end="2026-11-20T09:10:00+00:00")
    later = seed_item(status="timeboxed", title="later", timebox_end="2026-11-20T10:00:00+00:00")
    seed_item(status="tray", title="not timeboxed")

    data = client.get("/api/s2_timebox/timeboxes").json()

    assert data["server_now"] == "2026-11-20T09:00:00.250+00:00"
    assert data["server_now_ms"] == int(NOW.timestamp() * 1000)
    assert [i["id"] for i in data["items"]] == [soon.id, later.id]  # soonest first
    first = data["items"][0]
    assert first["remaining_seconds"] == pytest.approx(599.75, abs=0.001)
    assert first["timebox_end_ms"] - data["server_now_ms"] == 599_750
    assert first["expired"] is False


def test_expired_but_not_yet_swept_reports_zero_or_less(client):
    clock.set_now(NOW)
    seed_item(status="timeboxed", timebox_end="2026-11-20T08:59:59+00:00")
    item = client.get("/api/s2_timebox/timeboxes").json()["items"][0]
    assert item["remaining_seconds"] < 0
    assert item["expired"] is True


NODE_CHECK = r"""
import { clockSkewMs, remainingMs, formatRemaining } from "%s";
const serverNow = %d, end = %d;
const out = [];
// Browser clock is 90 s fast, 2 h slow, or exact; 40 ms round trip.
for (const offset of [90000, -7200000, 0]) {
  const sent = serverNow + offset - 20, received = serverNow + offset + 20;
  const skew = clockSkewMs(serverNow, sent, received);
  // Sample the display at several later moments.
  for (const elapsed of [0, 999, 1000, 61500, 599749]) {
    const clientNow = received + elapsed;
    const shown = remainingMs(end, clientNow, skew);
    const truth = end - (serverNow + 20 + elapsed);
    out.push([shown, truth, formatRemaining(shown)]);
  }
}
out.push(["fmt", formatRemaining(0), formatRemaining(-5), formatRemaining(1), formatRemaining(3_600_000), formatRemaining(3_661_001)]);
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_panel_countdown_maths_within_one_second_despite_clock_skew(tmp_path):
    module = tmp_path / "countdown.mjs"
    module.write_text((STATIC / "countdown.js").read_text(encoding="utf-8"), encoding="utf-8")
    server_now = int(NOW.timestamp() * 1000)
    end = int((NOW + timedelta(seconds=599.75)).timestamp() * 1000)
    script = NODE_CHECK % (module.as_uri(), server_now, end)

    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30, check=True,
    )
    rows = json.loads(res.stdout)
    fmt = rows.pop()
    for shown, truth, _text in rows:
        assert abs(shown - truth) < 1000
    assert fmt == ["fmt", "0:00", "0:00", "0:01", "1:00:00", "1:01:02"]
    # 599.75 s left is displayed rounded up, never early.
    assert rows[0][2] == "10:00"
