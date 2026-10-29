import httpx
import pytest

from app.infrastructure.email_delivery import (
    EmailDeliveryFailure,
    PostmarkEmailDelivery,
)


def _client(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=1.0,
    )


def test_postmark_adapter_sends_verification_message_without_tracking():
    token = "A" * 43
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"ErrorCode": 0, "Message": "OK", "MessageID": "message-id"},
        )

    with _client(handle) as client:
        delivery = PostmarkEmailDelivery(
            server_token="test-server-token",
            from_address="noreply@example.test",
            http_client=client,
        )
        delivery.send_verification("worker@example.test", token)

    request = requests[0]
    payload = request.read().decode("utf-8")
    assert request.method == "POST"
    assert request.url == "https://api.postmarkapp.com/email"
    assert request.headers["X-Postmark-Server-Token"] == "test-server-token"
    assert request.headers["Content-Type"] == "application/json"
    assert "worker@example.test" in payload
    assert token in payload
    assert "TrackOpens" not in payload
    assert "TrackLinks" not in payload


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="provider detail containing private data"),
        httpx.Response(200, json={"ErrorCode": 300, "Message": "private provider data"}),
    ],
)
def test_postmark_failures_do_not_expose_provider_or_message_secrets(
    response, caplog
):
    token = "B" * 43
    email = "worker@example.test"

    def handle(_request):
        return response

    with _client(handle) as client:
        delivery = PostmarkEmailDelivery(
            server_token="test-server-token",
            from_address="noreply@example.test",
            http_client=client,
        )
        with pytest.raises(EmailDeliveryFailure) as caught:
            delivery.send_password_reset(email, token)

    message = str(caught.value)
    assert "provider detail" not in message
    assert "private provider data" not in message
    assert token not in message
    assert email not in message
    assert "test-server-token" not in message
    assert token not in caplog.text
    assert email not in caplog.text
    assert "test-server-token" not in caplog.text
