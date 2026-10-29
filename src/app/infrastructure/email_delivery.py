import httpx


class EmailDeliveryFailure(Exception):
    """Raised when the transactional email provider rejects or cannot send a message."""


class PostmarkEmailDelivery:
    def __init__(
        self,
        *,
        server_token: str,
        from_address: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not server_token or not from_address:
            raise ValueError("Postmark credentials and sender address are required")
        self._server_token = server_token
        self._from_address = from_address
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.Client(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
        )

    def send_verification(self, email: str, token: str) -> None:
        self._send(
            email=email,
            token=token,
            subject="Verify your ShiftFlow account",
            instruction=(
                "Use this one-time code to verify your ShiftFlow account. "
                "It expires in 24 hours:"
            ),
        )

    def send_password_reset(self, email: str, token: str) -> None:
        self._send(
            email=email,
            token=token,
            subject="Reset your ShiftFlow password",
            instruction=(
                "Use this one-time code to reset your ShiftFlow password. "
                "It expires in 1 hour:"
            ),
        )

    def close(self) -> None:
        if self._owns_client:
            self._http_client.close()

    def _send(
        self,
        *,
        email: str,
        token: str,
        subject: str,
        instruction: str,
    ) -> None:
        try:
            response = self._http_client.post(
                "https://api.postmarkapp.com/email",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": self._server_token,
                },
                json={
                    "From": self._from_address,
                    "To": email,
                    "Subject": subject,
                    "TextBody": f"{instruction}\n\n{token}",
                    "MessageStream": "outbound",
                },
            )
            response.raise_for_status()
            provider_response = response.json()
            if provider_response.get("ErrorCode") != 0:
                raise EmailDeliveryFailure
        except (httpx.HTTPError, ValueError, AttributeError):
            raise EmailDeliveryFailure from None
        except EmailDeliveryFailure:
            raise
