"""Tests for provider_status_for_console in pro_auto.py."""

from unittest.mock import patch

from nexus_scalp.news.ai_service import NewsAIStatus, NewsAIStatusState
from nexus_scalp.news.pro_auto import provider_status_for_console


class MockProviderFull:
    def __init__(self):
        self.provider_name = "test_provider"
        self.model = "test_model"
        self.api_base_url = "https://test.local"

    def available(self):
        return True


class MockProviderPartial:
    def available(self):
        return False


def test_provider_status_with_valid_provider():
    mock_ai_status = NewsAIStatus()
    mock_ai_status.state = NewsAIStatusState.AVAILABLE
    mock_ai_status.configured = True

    with patch("nexus_scalp.news.pro_auto.get_ai_status", return_value=mock_ai_status):
        with patch(
            "nexus_scalp.news.pro_auto.resolve_factory_provider", return_value=MockProviderFull()
        ):
            result = provider_status_for_console(engine=None, settings_service=None)

            assert result["ai_status"]["state"] == "AVAILABLE"
            assert result["ai_status"]["configured"] is True
            assert result["provider_available"] is True
            assert result["provider_name"] == "test_provider"
            assert result["model"] == "test_model"
            assert result["base_url"] == "https://test.local"


def test_provider_status_with_no_provider():
    mock_ai_status = NewsAIStatus()
    mock_ai_status.state = NewsAIStatusState.NOT_CONFIGURED
    mock_ai_status.configured = False

    with patch("nexus_scalp.news.pro_auto.get_ai_status", return_value=mock_ai_status):
        with patch("nexus_scalp.news.pro_auto.resolve_factory_provider", return_value=None):
            result = provider_status_for_console(engine=None, settings_service=None)

            assert result["ai_status"]["state"] == "NOT_CONFIGURED"
            assert result["ai_status"]["configured"] is False
            assert result["provider_available"] is False
            assert result["provider_name"] == ""
            assert result["model"] == ""
            assert result["base_url"] == ""


def test_provider_status_with_partial_provider():
    mock_ai_status = NewsAIStatus()
    mock_ai_status.state = NewsAIStatusState.MISCONFIGURED
    mock_ai_status.configured = True

    with patch("nexus_scalp.news.pro_auto.get_ai_status", return_value=mock_ai_status):
        with patch(
            "nexus_scalp.news.pro_auto.resolve_factory_provider", return_value=MockProviderPartial()
        ):
            result = provider_status_for_console(engine=None, settings_service=None)

            assert result["ai_status"]["state"] == "MISCONFIGURED"
            assert result["ai_status"]["configured"] is True
            assert result["provider_available"] is False
            assert result["provider_name"] == ""
            assert result["model"] == ""
            assert result["base_url"] == ""
