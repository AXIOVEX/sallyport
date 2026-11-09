"""Shared test configuration.

The runner installs missing dependencies by default (engine runtime,
evaluator extension). Tests must never reach the network or build
environments by accident, so automatic installation is disabled for
every runner subprocess (they inherit os.environ) unless a test opts
back in explicitly, as tests/test_provisioning.py does.
"""
from __future__ import annotations

import os

os.environ["SPECKIT_AEE_AUTO_INSTALL"] = "0"
