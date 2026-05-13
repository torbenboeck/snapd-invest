namespace AlgoInvest.Client.Models;

public sealed record AgentRunResponse(
    string CorrelationId,
    string Agent,
    string Summary,
    string? RecommendationId
);
