"""Deny network at the socket boundary before test collection."""
import socket


def denied(*args, **kwargs):
    raise RuntimeError("RHEN staging forbids network access")


def pytest_configure(config):
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
