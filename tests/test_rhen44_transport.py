"""Real local WebSocket transport exercises; fixtures are never live evidence."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.command_visuals.publisher import LivePublisher
from app.market_fabric.stream_manager import MarketStreamManager
from app.market_fabric.stream_state import MarketStateStore


def test_actual_market_socket_handshake_and_event_processing():
    async def exercise():
        async def source(ws):
            await ws.send(json.dumps([{"T":"success","msg":"connected"}]))
            auth = json.loads(await ws.recv())
            assert auth["action"] == "auth"
            await ws.send(json.dumps([{"T":"success","msg":"authenticated"}]))
            subscription = json.loads(await ws.recv())
            assert subscription["quotes"] == ["SPY"]
            await ws.send(json.dumps([{"T":"subscription","quotes":["SPY"],"bars":["SPY"],"updatedBars":["SPY"]}]))
            now = datetime.now(timezone.utc)
            await ws.send(json.dumps([{"T":"b","S":"SPY","t":(now-timedelta(minutes=1)).isoformat(),"o":100,"h":102,"l":99,"c":101,"v":10},
                                      {"T":"q","S":"SPY","t":now.isoformat(),"bp":100,"ap":101}]))
        seen = []
        async def sink(event): seen.append(event)
        store = MarketStateStore(("SPY",),warm_bars=1)
        manager = MarketStreamManager(store,api_key="fixture",api_secret="fixture",on_event=sink)
        async with serve(source,"127.0.0.1",0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await manager.consume(ws,"iex","2026-10-06/REGULAR")
        assert [e.kind for e in seen] == ["bar","quote"]
        assert store.connection == "HEALTHY"
        assert store.snapshot("SPY",datetime.now(timezone.utc))["evaluable"]
    asyncio.run(exercise())


def test_actual_command_socket_subsecond_flush_and_critical_delivery():
    async def exercise():
        pub = LivePublisher(lambda:{"visual_schema":"command-visual.v1","system":{"entry_authority":False}},flush_ms=125)
        async def endpoint(ws):
            queue = pub.subscribe()
            try:
                while True:
                    message = await queue.get()
                    await ws.send(json.dumps(message))
            finally:
                pub.unsubscribe(queue)
        publisher_task = asyncio.create_task(pub.run())
        try:
            async with serve(endpoint,"127.0.0.1",0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as ws:
                    assert json.loads(await ws.recv())["message_type"] == "snapshot"
                    start = asyncio.get_running_loop().time()
                    pub.stage("SPY","scanner_patch",{"symbol":"SPY"})
                    batch = json.loads(await asyncio.wait_for(ws.recv(),.5))
                    latency = asyncio.get_running_loop().time()-start
                    assert batch["message_type"] == "delta_batch" and latency < .5
                    pub.send("critical_event",{"reason":"fixture"})
                    assert json.loads(await asyncio.wait_for(ws.recv(),.1))["message_type"] == "critical_event"
                # Queue receive has no websocket activity after close; cancel server handlers.
                for connection in server.connections:
                    connection.handler_task.cancel()
        finally:
            publisher_task.cancel()
            await asyncio.gather(publisher_task,return_exceptions=True)
    asyncio.run(exercise())
