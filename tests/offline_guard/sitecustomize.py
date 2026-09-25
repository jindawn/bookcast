"""Block real sockets in Python subprocesses spawned by offline pytest cases."""

import os

if os.environ.get("BOOKCAST_TEST_OFFLINE") == "1":
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline test attempted real network access")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    socket.getaddrinfo = forbidden
