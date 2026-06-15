"""Shared fixtures for the Away Mode test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom_components/ package in every test.

    pytest-homeassistant-custom-component does not load custom integrations by
    default; requesting its ``enable_custom_integrations`` fixture (autouse here)
    makes ``custom_components.away_mode`` importable and discoverable by HA.
    """
    yield
