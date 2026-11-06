# -*- coding: utf-8 -*-
"""Shared fixtures: new test modules use ``env``, ``addon``, ``qapp`` and
``frozen_now`` without importing them; helpers such as ``FakeNotion``,
``_rid``, ``_para``, ``P1``/``DB1`` are imported from
``tests.test_htmlify_and_sync``.
"""

from tests.test_htmlify_and_sync import (  # noqa: F401
    addon,
    env,
    frozen_now,
    qapp,
)
