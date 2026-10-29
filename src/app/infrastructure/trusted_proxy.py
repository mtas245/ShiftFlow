from ipaddress import ip_address, ip_network


class ClientAddressUnavailable(Exception):
    """Raised when a trusted proxy does not provide a valid client address."""


def resolve_client_address(
    *,
    peer_address: str | None,
    forwarded_for: str | None,
    trusted_proxy_cidrs: tuple[str, ...] = (),
) -> str:
    if not peer_address:
        raise ClientAddressUnavailable
    try:
        peer_ip = ip_address(peer_address)
        trusted_networks = tuple(
            ip_network(network, strict=False) for network in trusted_proxy_cidrs
        )
    except ValueError:
        if trusted_proxy_cidrs:
            raise ClientAddressUnavailable from None
        return peer_address

    if not any(peer_ip in network for network in trusted_networks):
        return str(peer_ip)
    if forwarded_for is None:
        raise ClientAddressUnavailable

    raw_addresses = [item.strip() for item in forwarded_for.split(",")]
    if not raw_addresses or any(not item for item in raw_addresses):
        raise ClientAddressUnavailable
    try:
        chain = [ip_address(item) for item in raw_addresses]
    except ValueError:
        raise ClientAddressUnavailable from None

    current_address = peer_ip
    for forwarded_address in reversed(chain):
        if not any(current_address in network for network in trusted_networks):
            break
        current_address = forwarded_address
    return str(current_address)


def parse_trusted_proxy_cidrs(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()
    networks = tuple(item.strip() for item in value.split(","))
    try:
        for network in networks:
            ip_network(network, strict=False)
    except ValueError:
        raise ClientAddressUnavailable from None
    if any(not network for network in networks):
        raise ClientAddressUnavailable
    return networks
