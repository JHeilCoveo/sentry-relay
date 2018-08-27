import pytest
import gzip
from datetime import datetime

import requests

import sentry_sdk

from flask import request, Response


@pytest.mark.parametrize("compress_request", (True, False))
@pytest.mark.parametrize("compress_response", (True, False))
def test_forwarding(compress_request, compress_response, mini_sentry, relay_chain):
    data = b"foobar"

    @mini_sentry.app.route("/test/reflect", methods=["POST"])
    def test():
        _data = request.data
        if request.headers.get("Content-Encoding", "") == "gzip":
            _data = gzip.decompress(_data)

        assert _data == data

        headers = {}

        if compress_response:
            _data = gzip.compress(_data)
            headers["Content-Encoding"] = "gzip"

        return Response(_data, headers=headers)

    relay_chain.wait_relay_healthcheck()

    headers = {"Content-Type": "application/octet-stream"}

    if compress_request:
        payload = gzip.compress(data)
        headers["Content-Encoding"] = "gzip"
    else:
        payload = data

    response = requests.post(
        relay_chain.url + "/test/reflect", data=payload, headers=headers
    )
    response.raise_for_status()
    assert response.content == data


def test_store(mini_sentry, relay, gobetween):
    r0 = relay(mini_sentry)
    r = relay(r0)

    trusted_relays = [r0.public_key, r.public_key]

    mini_sentry.project_configs[42] = {
        "publicKeys": {"31a5a894b4524f74a9a8d0e27e21ba91": True},
        "rev": "5ceaea8c919811e8ae7daae9fe877901",
        "disabled": False,
        "lastFetch": "2018-08-24T17:29:04.426Z",
        "lastChange": "2018-07-27T12:27:01.481Z",
        "config": {
            "allowedDomains": ["*"],
            "trustedRelays": trusted_relays,
            "piiConfig": {
                "rules": {},
                "applications": {
                    "freeform": ["@email", "@mac", "@creditcard", "@userpath"],
                    "username": ["@userpath"],
                    "ip": [],
                    "databag": [
                        "@email",
                        "@mac",
                        "@creditcard",
                        "@userpath",
                        "@password",
                    ],
                    "email": ["@email"],
                },
            },
        },
        "slug": "python",
    }

    r.wait_relay_healthcheck()

    client = sentry_sdk.Client(r.dsn, default_integrations=False)
    hub = sentry_sdk.Hub(client)
    hub.add_breadcrumb(level="info", message="i like bread", timestamp=datetime.now())
    hub.capture_message("hü")
    client.drain_events()

    event = mini_sentry.captured_events.get(timeout=30)
    assert mini_sentry.captured_events.empty()

    if isinstance(event["breadcrumbs"], dict):
        crumbs = event["breadcrumbs"]["values"]
    else:
        crumbs = event["breadcrumbs"]

    assert any(crumb["message"] == "i like bread" for crumb in crumbs)
    assert event["message"] == "hü"


def test_limits(mini_sentry, relay):
    @mini_sentry.app.route(
        "/api/0/projects/<org>/<project>/releases/<release>/files/", methods=["POST"]
    )
    def dummy_upload(**opts):
        return Response(request.data, content_type="application/octet-stream")

    relay = relay(mini_sentry)
    relay.wait_relay_healthcheck()

    response = requests.post(
        relay.url + "/api/0/projects/a/b/releases/1.0/files/",
        data="Hello",
        headers={"Content-Type": "text/plain"},
    )
    assert response.content == b"Hello"

    response = requests.post(
        relay.url + "/api/0/projects/a/b/releases/1.0/files/",
        data=b"x" * (1024 * 1024 * 2),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 413
