namespace AlgoInvest.Client.Models;

public sealed record PositionDto(
    string InstrumentSymbol,
    string InstrumentExchange,
    decimal Quantity,
    decimal AvgCost,
    decimal? LastPrice,
    decimal? MarketValue,
    decimal? UnrealizedPnl,
    string Tag
);

public sealed record PortfolioResponse(
    string AccountId,
    string AccountName,
    string BaseCurrency,
    decimal Cash,
    decimal? Equity,
    List<PositionDto> Positions
);
