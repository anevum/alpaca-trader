# Broker capability facts used by RHEN 4.4 package

Verified against Alpaca documentation during package preparation on 2026-10-06 ET.

- Basic Trading API market data is free, real-time equities coverage is IEX, and stock WebSocket subscription limit is 30 symbols.
- Algo Trader Plus is currently $99/month, supplies all-U.S.-exchange/SIP equities coverage, and permits unlimited stock WebSocket subscriptions.
- Stock WebSocket feed identifiers include `v2/iex`, `v2/sip`, `v1beta1/boats`, and `v1beta1/overnight`.
- Alpaca recommends real-time stream use rather than polling latest historical endpoints when current pricing matters.
- Basic overnight feed provides real-time indicative latest quotes; trade information is delayed. `overnight_tradable` and `overnight_halted` asset fields must be honored.
- Overnight orders are limit-only; current Alpaca documentation should be treated as authority for exact supported TIF behavior at implementation time.
- Trading WebSocket `trade_updates` provides order/account/trade lifecycle updates.

Authoritative documentation:

- https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api
- https://docs.alpaca.markets/us/v1.1/docs/real-time-stock-pricing-data
- https://docs.alpaca.markets/us/docs/245-trading-for-trading-api
- https://docs.alpaca.markets/us/docs/websocket-streaming
- https://docs.alpaca.markets/us/docs/orders-at-alpaca
