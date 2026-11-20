"""S1 test defaults: S7 (links) is switched off unless a test installs a fake.

S1 must behave exactly as before without S7, and S7's own LLM calls must not
consume the stub replies that S1 tests queue. Tests that need links use the
``fake_s7`` fixture in ``test_s1_links.py``.
"""

import sys

import pytest

from taskbot.slices.s1_intake import service


@pytest.fixture(autouse=True)
def s7_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, service.S7_MODULE, None)
