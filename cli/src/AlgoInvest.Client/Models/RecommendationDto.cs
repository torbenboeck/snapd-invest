namespace AlgoInvest.Client.Models;

public sealed record RecommendationDto(
    string Id,
    string AgentId,
    string Status,
    string Rationale,
    string Signals,
    string? CorrelationId,
    string CreatedAt,
    string ExpiresAt,
    string? ResolvedAt
);

public sealed record SignalModificationDto(
    string InstrumentSymbol,
    string InstrumentExchange,
    decimal? Quantity,
    bool Skip
);

public sealed record ApproveRequest(
    List<SignalModificationDto>? Modifications
);

public sealed record ApproveResponse(
    string RecommendationId,
    string Status,
    List<Dictionary<string, object?>> ExecutionSummaries
);

public sealed record RejectRequest(
    string? Reason
);
