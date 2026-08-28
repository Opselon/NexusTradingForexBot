import pytest

from src.nexus_scalp.news.sources.base import (
    OfficialSourceAdapter,
    RSSNewsSourceAdapter,
    build_adapter,
)


def test_build_adapter_default():
    config = {}
    adapter = build_adapter(config)
    assert isinstance(adapter, RSSNewsSourceAdapter)
    assert not isinstance(adapter, OfficialSourceAdapter)


def test_build_adapter_rss():
    config = {"kind": "RSS"}
    adapter = build_adapter(config)
    assert isinstance(adapter, RSSNewsSourceAdapter)
    assert not isinstance(adapter, OfficialSourceAdapter)


def test_build_adapter_official():
    config = {"kind": "OFFICIAL"}
    adapter = build_adapter(config)
    assert isinstance(adapter, OfficialSourceAdapter)


def test_build_adapter_calendar():
    config = {"kind": "CALENDAR"}
    adapter = build_adapter(config)
    assert isinstance(adapter, OfficialSourceAdapter)


def test_build_adapter_case_insensitive():
    config = {"kind": "official"}
    adapter = build_adapter(config)
    assert isinstance(adapter, OfficialSourceAdapter)


def test_build_adapter_unknown():
    config = {"kind": "UNKNOWN_KIND"}
    adapter = build_adapter(config)
    assert isinstance(adapter, RSSNewsSourceAdapter)
    assert not isinstance(adapter, OfficialSourceAdapter)
