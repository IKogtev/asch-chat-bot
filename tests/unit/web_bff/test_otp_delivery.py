from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_bff.otp_delivery import deliver_otp_code


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deliver_otp_posts_to_bot_with_secret() -> None:
    settings = MagicMock(
        otp_internal_secret="s3cret",
        bot_telegram_api="http://bot:8001",
        bot_max_api="http://bot-max:8002",
    )
    response = AsyncMock()
    response.status = 200
    response.__aenter__.return_value = response
    response.__aexit__.return_value = False
    session = AsyncMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False

    with patch("web_bff.otp_delivery.aiohttp.ClientSession", return_value=session):
        sent = await deliver_otp_code(
            settings,
            [{"platform": "telegram", "platform_user_id": 42}],
            "123456",
        )

    assert sent == 1
    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0] == "http://bot:8001/internal/otp"
    assert kwargs["json"] == {"platform_user_id": 42, "code": "123456"}
    assert kwargs["headers"]["X-Internal-Otp-Key"] == "s3cret"
