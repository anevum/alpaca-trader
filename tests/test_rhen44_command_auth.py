from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.main as main
from app.command_visuals.publisher import LivePublisher


def isolated_app():
    # Reuse the actual endpoint without starting champion or research lifespans.
    app = FastAPI()
    app.add_api_websocket_route("/v1/command/stream",main.command_live_stream)
    return app


def test_unauthorized_socket_rejected_before_snapshot(monkeypatch):
    monkeypatch.setattr(main,"require_command_admin",AsyncMock(side_effect=HTTPException(401,"Unauthorized")))
    with TestClient(isolated_app()) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/v1/command/stream"): pass
    assert error.value.code == 1008


def test_history_read_preserves_authentication_and_disabled_gate(monkeypatch):
    app = FastAPI()
    app.add_api_route("/history",main.command_shadow_history,methods=["GET"])
    monkeypatch.setattr(main,"require_command_admin",AsyncMock(side_effect=HTTPException(401,"Unauthorized")))
    params={"series":"candles:SPY","start":"2026-10-07T14:00:00Z","end":"2026-10-07T15:00:00Z"}
    with TestClient(app) as client:
        assert client.get("/history",params=params).status_code == 401
        monkeypatch.setattr(main,"require_command_admin",AsyncMock(return_value={"email":"fixture@example.invalid"}))
        monkeypatch.setattr(main,"settings",main.settings.model_copy(update={"command_live_stream_enabled":False}))
        monkeypatch.setattr(main,"shadow_fabric",None)
        assert client.get("/history",params=params).status_code == 503


def test_disabled_socket_does_not_activate_observer(monkeypatch):
    monkeypatch.setattr(main,"require_command_admin",AsyncMock(return_value={"email":"fixture@example.invalid"}))
    monkeypatch.setattr(main,"settings",main.settings.model_copy(update={"command_live_stream_enabled":False}))
    monkeypatch.setattr(main,"shadow_fabric",None)
    with TestClient(isolated_app()) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/v1/command/stream",headers={"authorization":"Bearer test"}): pass
    assert error.value.code == 1013 and main.shadow_fabric is None


def test_authorized_socket_snapshot_and_subscription_cleanup(monkeypatch):
    monkeypatch.setattr(main,"require_command_admin",AsyncMock(return_value={"email":"fixture@example.invalid"}))
    monkeypatch.setattr(main,"settings",main.settings.model_copy(update={"command_live_stream_enabled":True}))
    pub = LivePublisher(lambda:{"visual_schema":"command-visual.v1","system":{"entry_authority":False}})
    monkeypatch.setattr(main,"shadow_fabric",SimpleNamespace(visual=SimpleNamespace(publisher=pub)))
    token = jwt.encode({"exp":(datetime.now(timezone.utc)+timedelta(seconds=30)).timestamp()},"fixture-only",algorithm="HS256")
    with TestClient(isolated_app()) as client:
        with client.websocket_connect("/v1/command/stream",headers={"authorization":"Bearer "+token}) as ws:
            snapshot = ws.receive_json()
            assert snapshot["message_type"] == "snapshot"
            assert snapshot["payload"]["system"]["entry_authority"] is False
    assert not pub.clients


def test_verified_assertion_expiry_closes_existing_socket(monkeypatch):
    monkeypatch.setattr(main,"require_command_admin",AsyncMock(return_value={"email":"fixture@example.invalid"}))
    monkeypatch.setattr(main,"settings",main.settings.model_copy(update={"command_live_stream_enabled":True}))
    pub = LivePublisher(lambda:{"visual_schema":"command-visual.v1","system":{}})
    monkeypatch.setattr(main,"shadow_fabric",SimpleNamespace(visual=SimpleNamespace(publisher=pub)))
    token = jwt.encode({"exp":(datetime.now(timezone.utc)-timedelta(seconds=1)).timestamp()},"fixture-only",algorithm="HS256")
    with TestClient(isolated_app()) as client:
        with client.websocket_connect("/v1/command/stream",headers={"authorization":"Bearer "+token}) as ws:
            with pytest.raises(WebSocketDisconnect) as error: ws.receive_json()
    assert error.value.code == 1008 and not pub.clients
