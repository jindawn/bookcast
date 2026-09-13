"""Bounded HTTPS GET with validated, pinned public IPs and no ambient credentials."""

import hashlib
import http.client
import io
import ipaddress
import math
from pathlib import Path
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin, unquote

from .errors import BookCastError
from .storage import atomic_target


def public_url(url: str) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.port not in {None, 443}
                or parts.username or parts.password or parts.query or parts.fragment
                or "\\" in url or any(c.isspace() or ord(c) < 32 for c in unquote(url))):
            raise ValueError()
        return parts.hostname.encode("idna").decode(), parts.path or "/"
    except (ValueError, UnicodeError):
        raise BookCastError("来源 URL 必须为公开 HTTPS 地址（443），不能包含凭证、query、fragment 或控制字符。") from None


def public_addresses(host: str) -> list[str]:
    try:
        addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(ip).is_global
                                or ipaddress.ip_address(ip).is_multicast for ip in addresses):
            raise BookCastError("来源地址不能指向本机、私网、保留地址或非公开网络。")
        return sorted(addresses, key=lambda ip: ":" in ip)
    except (OSError, ValueError):
        raise BookCastError("无法解析来源主机的公开地址。") from None


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class SafeHTTP:
    def __init__(self, timeout: float = 20, total_timeout: float = 120, address_overrides: dict[str, str] | None = None):
        if not (math.isfinite(timeout) and 0 < timeout <= 120 and math.isfinite(total_timeout) and 0 < total_timeout <= 900):
            raise BookCastError("网络超时必须为有限正数：连接/读取最多 120 秒，总时限最多 900 秒。")
        self.timeout, self.total_timeout = timeout, total_timeout
        self.address_overrides = {}
        for host, address in (address_overrides or {}).items():
            normalized, path = public_url(f"https://{host}/")
            try:
                ip = ipaddress.ip_address(address)
                if not ip.is_global or ip.is_multicast or path != "/" or any(c in host for c in "/:@?#"):
                    raise ValueError()
            except ValueError:
                raise BookCastError("--resolve 只接受主机名=公网 IP；不能覆盖到私网或保留地址。") from None
            self.address_overrides[normalized] = str(ip)

    def _transfer(self, url, stream, max_bytes, allowed_mimes):
        if max_bytes <= 0:
            raise BookCastError("下载大小上限必须为正数。")
        start = time.monotonic()
        for hop in range(4):
            host, path = public_url(url)
            addresses = [self.address_overrides[host]] if host in self.address_overrides else public_addresses(host)
            connection = PinnedHTTPSConnection(host, addresses[0], self.timeout)
            try:
                connection.request("GET", path, headers={"User-Agent": "BookCast/0.1 (local ebook resolver)",
                                                         "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or hop == 3:
                        raise BookCastError("下载重定向缺失或超过三次。")
                    url = urljoin(url, location)
                    # Next hop must pass URL, DNS and TLS verification again.
                    continue
                if response.status != 200:
                    raise BookCastError(f"来源 HTTP 错误：{response.status}。")
                mime = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
                if mime not in allowed_mimes:
                    raise BookCastError("来源 MIME 类型不符合预期。")
                if (response.getheader("Content-Encoding") or "identity").lower() != "identity":
                    raise BookCastError("不接受压缩 HTTP 响应。")
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                    raise BookCastError("来源大小超过限制或长度无效。")
                count, digest = 0, hashlib.sha256()
                while True:
                    if time.monotonic() - start > self.total_timeout:
                        raise BookCastError("下载超过总时间限制。")
                    chunk = response.read1(min(64 * 1024, max_bytes - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > max_bytes:
                        raise BookCastError("下载实际大小超过限制。")
                    digest.update(chunk)
                    stream.write(chunk)
                if count == 0 or (length is not None and count != int(length)):
                    raise BookCastError("下载为空或内容被截断。")
                return {"url": url, "mime": mime, "size": count, "sha256": digest.hexdigest()}
            except (OSError, http.client.HTTPException):
                raise BookCastError("来源连接、TLS 或传输失败；没有保存不完整文件。") from None
            finally:
                connection.close()
        raise BookCastError("下载无法完成。")

    def fetch(self, url: str, *, max_bytes: int, allowed_mimes: set[str]) -> tuple[bytes, dict]:
        stream = io.BytesIO()
        receipt = self._transfer(url, stream, max_bytes, allowed_mimes)
        return stream.getvalue(), receipt

    def download(self, url: str, destination: Path, *, max_bytes: int, allowed_mimes: set[str], validate=None) -> dict:
        with atomic_target(destination) as temporary:
            with temporary.open("wb") as stream:
                receipt = self._transfer(url, stream, max_bytes, allowed_mimes)
            if validate:
                validate(temporary)
        return receipt
