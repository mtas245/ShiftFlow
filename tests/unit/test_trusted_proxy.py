import pytest

from app.infrastructure.trusted_proxy import (
    ClientAddressUnavailable,
    resolve_client_address,
)


def test_ignores_forwarded_address_from_untrusted_peer():
    assert (
        resolve_client_address(
            peer_address="203.0.113.8",
            forwarded_for="198.51.100.9",
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "203.0.113.8"
    )


def test_resolves_client_through_trusted_proxy_chain():
    assert (
        resolve_client_address(
            peer_address="10.0.0.2",
            forwarded_for="198.51.100.9, 10.0.0.1",
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "198.51.100.9"
    )


@pytest.mark.parametrize(
    "forwarded_for",
    [None, "", "attacker-value", "198.51.100.9, invalid"],
)
def test_malformed_forwarded_chain_from_trusted_peer_fails_closed(forwarded_for):
    with pytest.raises(ClientAddressUnavailable):
        resolve_client_address(
            peer_address="10.0.0.2",
            forwarded_for=forwarded_for,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )


def test_invalid_trusted_proxy_cidr_is_rejected():
    with pytest.raises(ClientAddressUnavailable):
        resolve_client_address(
            peer_address="10.0.0.2",
            forwarded_for="198.51.100.9",
            trusted_proxy_cidrs=("not-a-cidr",),
        )
