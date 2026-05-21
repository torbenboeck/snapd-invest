namespace SnapdInvest.Client.Models;

// snake_case naming applied by the global PropertyNamingPolicy in Program.cs.

public sealed record PlaceOrderRequest(
    string AccountId,
    string InstrumentSymbol,
    string InstrumentExchange,
    string Side,
    decimal Quantity,
    decimal? LimitPrice = null,
    string Source = "manual-cli"
);

public sealed record PlaceOrderResponse(
    string Kind,
    string? OrderId,
    string? Reason,
    string? SaxoErrorCode
);
