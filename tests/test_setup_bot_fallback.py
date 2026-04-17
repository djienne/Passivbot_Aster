"""Tests for setup_bot() dispatch behavior — Aster-only fork."""

import pytest
from unittest.mock import patch, MagicMock


def test_setup_bot_aster_uses_specific_bot():
    """setup_bot() uses the custom Aster bot for exchange=aster."""
    from passivbot import setup_bot

    config = {"live": {"user": "test_user"}}

    mock_user_info = {
        "exchange": "aster",
        "api_user": "0x1111111111111111111111111111111111111111",
        "api_signer": "0x2222222222222222222222222222222222222222",
        "api_private_key": "0xabc",
    }

    with patch("passivbot.load_user_info", return_value=mock_user_info):
        with patch("exchanges.aster.AsterBot") as mock_aster_bot:
            mock_bot = MagicMock()
            mock_aster_bot.return_value = mock_bot

            result = setup_bot(config)

            mock_aster_bot.assert_called_once_with(config)
            assert result == mock_bot


@pytest.mark.parametrize("exchange", ["hyperliquid", "binance", "bybit", "kraken", ""])
def test_setup_bot_non_aster_raises_runtimeerror(exchange):
    """setup_bot() raises a clear RuntimeError for any non-aster exchange."""
    from passivbot import setup_bot

    config = {"live": {"user": "test_user"}}
    mock_user_info = {"exchange": exchange}

    with patch("passivbot.load_user_info", return_value=mock_user_info):
        with pytest.raises(RuntimeError) as excinfo:
            setup_bot(config)
    assert "aster" in str(excinfo.value).lower()
    assert exchange == "" or exchange in str(excinfo.value)
