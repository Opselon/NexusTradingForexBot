from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.news.ai_service import NewsAIStatus, NewsAIStatusState
from nexus_scalp.news.pro_auto import provider_status_for_console


@patch("nexus_scalp.news.pro_auto.get_ai_status")
@patch("nexus_scalp.news.pro_auto.resolve_factory_provider")
def test_provider_status_for_console_success(mock_resolve_provider, mock_get_ai_status):
    # Setup mocks
    mock_status = NewsAIStatus(
        configured=True,
        available=True,
        state=NewsAIStatusState.AVAILABLE,
        provider="test_provider",
        model="test_model",
        base_url="https://test.url",
        source="test_source",
        detail="OK",
    )
    mock_get_ai_status.return_value = mock_status

    mock_provider = MagicMock()
    mock_provider.available.return_value = True
    mock_provider.provider_name = "MockedProvider"
    mock_provider.model = "gpt-4-test"
    mock_provider.api_base_url = "https://mock.provider.com/v1"
    mock_resolve_provider.return_value = mock_provider

    # Call function
    result = provider_status_for_console(engine=None, settings_service=None)

    # Assertions
    assert result["ai_status"] == mock_status.to_dict()
    assert result["provider_available"] is True
    assert result["provider_name"] == "MockedProvider"
    assert result["model"] == "gpt-4-test"
    assert result["base_url"] == "https://mock.provider.com/v1"

    mock_get_ai_status.assert_called_once_with(None, None)
    mock_resolve_provider.assert_called_once_with(None, None)


@patch("nexus_scalp.news.pro_auto.get_ai_status")
@patch("nexus_scalp.news.pro_auto.resolve_factory_provider")
def test_provider_status_for_console_no_provider(mock_resolve_provider, mock_get_ai_status):
    # Setup mocks
    mock_status = NewsAIStatus(
        configured=False,
        available=False,
        state=NewsAIStatusState.NOT_CONFIGURED,
    )
    mock_get_ai_status.return_value = mock_status

    # resolve_factory_provider returns None when provider is not configured
    mock_resolve_provider.return_value = None

    # Call function
    result = provider_status_for_console(engine=None, settings_service=None)

    # Assertions
    assert result["ai_status"] == mock_status.to_dict()
    assert result["provider_available"] is False
    assert result["provider_name"] == ""
    assert result["model"] == ""
    assert result["base_url"] == ""

    mock_get_ai_status.assert_called_once_with(None, None)
    mock_resolve_provider.assert_called_once_with(None, None)


@patch("nexus_scalp.news.pro_auto.get_ai_status")
@patch("nexus_scalp.news.pro_auto.resolve_factory_provider")
def test_provider_status_for_console_provider_unavailable(
    mock_resolve_provider, mock_get_ai_status
):
    # Setup mocks
    mock_status = NewsAIStatus(
        configured=True,
        available=False,
        state=NewsAIStatusState.UNAVAILABLE,
    )
    mock_get_ai_status.return_value = mock_status

    mock_provider = MagicMock()
    mock_provider.available.return_value = False
    mock_provider.provider_name = "MockedProvider"
    mock_provider.model = "gpt-4-test"
    mock_provider.api_base_url = "https://mock.provider.com/v1"
    mock_resolve_provider.return_value = mock_provider

    # Call function
    result = provider_status_for_console(engine=None, settings_service=None)

    # Assertions
    assert result["ai_status"] == mock_status.to_dict()
    assert result["provider_available"] is False
    assert result["provider_name"] == "MockedProvider"
    assert result["model"] == "gpt-4-test"
    assert result["base_url"] == "https://mock.provider.com/v1"

    mock_get_ai_status.assert_called_once_with(None, None)
    mock_resolve_provider.assert_called_once_with(None, None)
