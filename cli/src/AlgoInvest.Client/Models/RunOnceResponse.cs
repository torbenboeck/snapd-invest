namespace AlgoInvest.Client.Models;

public sealed record SignalDto(
    string Source,
    string InstrumentSymbol,
    string InstrumentExchange,
    string Action,
    decimal Quantity,
    decimal Conviction,
    string Rationale
);

public sealed record RunOnceResponse(
    string CorrelationId,
    string Strategy,
    List<SignalDto> Signals,
    List<Dictionary<string, object?>> Outcomes
);
