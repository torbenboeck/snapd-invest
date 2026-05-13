namespace SnapdInvest.Client.Models;

public sealed record AuditEventDto(
    string Id,
    string Type,
    string Payload,
    string? CorrelationId,
    string OccurredAt
);
