"""Security tests exercise real downloader logic with in-memory HTTP transport."""

import io
import socket
from unittest.mock import MagicMock, patch

import pytest

from bookcast.errors import BookCastError
from bookcast.source_http import SafeHTTP, PinnedHTTPSConnection, public_url, public_addresses
from bookcast.source_validation import validate_source


class Response(io.BytesIO):
    def __init__(self, body=b"Chapter 1\nSample text.", status=200, **headers):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": "text/plain", **headers}

    def getheader(self, name):
        return self.headers.get(name)


@pytest.fixture
def transport():
    with patch("bookcast.source_http.public_addresses", return_value=["93.184.216.34"]) as dns, \
            patch("bookcast.source_http.PinnedHTTPSConnection") as connection:
        connection.return_value.getresponse.return_value = Response()
        yield connection, dns


def test_success_ignores_server_filename_and_has_no_ambient_credentials(tmp_path, transport, monkeypatch):
    connection, _ = transport
    connection.return_value.getresponse.return_value = Response(**{"Content-Disposition": 'attachment; filename="../../evil.py"'})
    monkeypatch.setenv("HTTPS_PROXY", "https://user:secret@proxy.invalid")
    target = tmp_path / "safe.txt"
    receipt = SafeHTTP().download("https://example.org/book", target, max_bytes=1024,
                                  allowed_mimes={"text/plain"}, validate=lambda p: validate_source(p, "txt", 1024))
    assert target.is_file() and receipt["size"] == target.stat().st_size
    assert len(receipt["sha256"]) == 64 and sorted(p.name for p in tmp_path.iterdir()) == ["safe.txt"]
    args = connection.return_value.request.call_args
    assert args.args == ("GET", "/book")
    assert "Authorization" not in args.kwargs["headers"] and "Cookie" not in args.kwargs["headers"]
    connection.assert_called_once_with("example.org", "93.184.216.34", 20)


@pytest.mark.parametrize("response,limit", [
    (Response(**{"Content-Type": "text/html"}), 100),
    (Response(**{"Content-Type": "application/octet-stream"}), 100),
    (Response(**{"Content-Type": ""}), 100),
    (Response(**{"Content-Length": "999"}), 100),
    (Response(**{"Content-Length": "-1"}), 100),
    (Response(**{"Content-Length": "2, 2"}), 100),
    (Response(b"123456", **{"Content-Length": "10"}), 100),
    (Response(b""), 100),
    (Response(b"123456"), 5),
    (Response(**{"Content-Encoding": "gzip"}), 100),
    (Response(status=403), 100),
])
def test_rejected_responses_never_replace_existing_file(tmp_path, transport, response, limit):
    connection, _ = transport
    connection.return_value.getresponse.return_value = response
    target = tmp_path / "book.txt"
    target.write_text("existing user data")
    with pytest.raises(BookCastError):
        SafeHTTP().download("https://example.org/book", target, max_bytes=limit, allowed_mimes={"text/plain"})
    assert target.read_text() == "existing user data" and len(list(tmp_path.iterdir())) == 1


def test_signature_failure_is_atomic(tmp_path, transport):
    connection, _ = transport
    connection.return_value.getresponse.return_value = Response(b"<html>login</html>")
    target = tmp_path / "book.txt"
    with pytest.raises(BookCastError):
        SafeHTTP().download("https://example.org/book", target, max_bytes=1024, allowed_mimes={"text/plain"},
                            validate=lambda p: validate_source(p, "txt", 1024))
    assert not target.exists() and not list(tmp_path.iterdir())


@pytest.mark.parametrize("url", ["http://example.org/book", "file:///etc/passwd", "https://user:secret@example.org/a",
    "https://example.org/a?token=secret", "https://example.org/a#fragment", "https://example.org:8443/a",
    "https://example.org/a%0d%0aHeader:yes", "https://example.org/a\\b", "https:///a"])
def test_unsafe_url_rejected_without_network(url):
    with patch("bookcast.source_http.socket.getaddrinfo") as dns:
        with pytest.raises(BookCastError):
            public_url(url)
        dns.assert_not_called()


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.1.2", "169.254.169.254", "::1", "fc00::1",
                                "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_private_and_nonpublic_dns_results_rejected(ip):
    results = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
    with patch("bookcast.source_http.socket.getaddrinfo", return_value=results):
        with pytest.raises(BookCastError):
            public_addresses("public-looking.example")


def test_redirect_revalidates_destination_and_never_connects_private_ip(tmp_path, transport):
    connection, dns = transport
    connection.return_value.getresponse.return_value = Response(status=302, Location="https://127.0.0.1/private")
    dns.side_effect = [["93.184.216.34"], BookCastError("private address")]
    with pytest.raises(BookCastError):
        SafeHTTP().download("https://example.org/book", tmp_path / "book.txt", max_bytes=1024, allowed_mimes={"text/plain"})
    assert connection.call_count == 1 and dns.call_count == 2


def test_redirect_limit(tmp_path, transport):
    connection, _ = transport
    connection.return_value.getresponse.return_value = Response(status=302, Location="/again")
    with pytest.raises(BookCastError, match="三次"):
        SafeHTTP().download("https://example.org/book", tmp_path / "book.txt", max_bytes=1024, allowed_mimes={"text/plain"})
    assert connection.call_count == 4


def test_connection_uses_validated_ip_and_original_hostname_for_tls():
    connection = PinnedHTTPSConnection("example.org", "93.184.216.34", 20)
    connection._context = MagicMock()
    with patch("bookcast.source_http.socket.create_connection") as connect:
        connection.connect()
        connect.assert_called_once_with(("93.184.216.34", 443), 20)
        connection._context.wrap_socket.assert_called_once_with(connect.return_value, server_hostname="example.org")


def test_timeout_leaves_no_partial_file(tmp_path, transport):
    connection, _ = transport
    connection.return_value.getresponse.side_effect = TimeoutError()
    with pytest.raises(BookCastError):
        SafeHTTP().download("https://example.org/book", tmp_path / "book.txt", max_bytes=1024, allowed_mimes={"text/plain"})
    assert not list(tmp_path.iterdir())


def test_explicit_public_address_override_preserves_hostname(transport):
    connection, dns = transport
    SafeHTTP(address_overrides={"example.org": "93.184.216.34"}).fetch(
        "https://example.org/book", max_bytes=1024, allowed_mimes={"text/plain"})
    dns.assert_not_called()
    connection.assert_called_once_with("example.org", "93.184.216.34", 20)


@pytest.mark.parametrize("address", ["198.18.0.37", "127.0.0.1", "not-an-ip", "::1"])
def test_address_override_cannot_disable_private_network_protection(address):
    with pytest.raises(BookCastError):
        SafeHTTP(address_overrides={"example.org": address})


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 901])
def test_total_timeout_cannot_be_unbounded(timeout):
    with pytest.raises(BookCastError):
        SafeHTTP(total_timeout=timeout)
