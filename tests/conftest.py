# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Shared test fixtures.

The whole bridge funnels through ``_adb.adb()``, which is the only place that
shells out (``subprocess.run``). So patching ``_adb.subprocess.run`` is the one
mock point that lets every module run device-free and deterministically: tests
inspect the argv that *would* have been sent and feed back canned stdout.
"""

from __future__ import annotations

import types

import pytest


class FakeRun:
    """Stand-in for ``subprocess.run`` that records argv and returns canned output.

    Set ``stdout``/``returncode`` for a single fixed reply, or ``responder`` (a
    callable ``argv -> (stdout_bytes, returncode)``) to vary by command.
    """

    def __init__(self):
        self.calls: list[list[str]] = []   # full argv incl. leading "adb"
        self.stdout: bytes = b""
        self.returncode: int = 0
        self.responder = None

    def __call__(self, argv, capture_output=True, timeout=None):
        self.calls.append(list(argv))
        if self.responder is not None:
            out, rc = self.responder(argv)
        else:
            out, rc = self.stdout, self.returncode
        if isinstance(out, str):
            out = out.encode()
        return types.SimpleNamespace(stdout=out, returncode=rc, stderr=b"")

    @property
    def last(self) -> list[str]:
        """The most recent full argv (including the leading 'adb')."""
        return self.calls[-1]

    @property
    def last_args(self) -> list[str]:
        """The most recent argv with the leading 'adb' stripped (no -s serial,
        since the fixture clears ANDROID_SERIAL)."""
        return self.calls[-1][1:]


@pytest.fixture
def fake_run(monkeypatch):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    fr = FakeRun()
    monkeypatch.setattr("atak_mcp.bridge._adb.subprocess.run", fr)
    return fr
