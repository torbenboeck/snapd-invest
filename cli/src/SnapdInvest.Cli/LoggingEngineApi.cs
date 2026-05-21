using Microsoft.Extensions.Logging;
using Refit;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;

namespace SnapdInvest.Cli;

/// Decorator over <see cref="IEngineApi"/> that emits one Information log per
/// successful call and one Warning per <see cref="ApiException"/>. Keeps each
/// command thin — no per-command logging boilerplate. Sits on top of the
/// Refit-generated implementation registered by <c>AddRefitClient</c>.
internal sealed partial class LoggingEngineApi(IEngineApi inner, ILogger<LoggingEngineApi> logger)
    : IEngineApi
{
    public Task<HealthResponse> GetHealthAsync(CancellationToken ct = default)
        => Invoke(nameof(GetHealthAsync), null, () => inner.GetHealthAsync(ct));

    public Task<PortfolioResponse> GetPortfolioAsync(
        string accountName = "paper", CancellationToken ct = default)
        => Invoke(
            nameof(GetPortfolioAsync), $"accountName={accountName}",
            () => inner.GetPortfolioAsync(accountName, ct));

    public Task<List<AuditEventDto>> GetAuditAsync(
        int limit = 100, string? eventType = null, CancellationToken ct = default)
        => Invoke(
            nameof(GetAuditAsync), $"limit={limit} type={eventType ?? "-"}",
            () => inner.GetAuditAsync(limit, eventType, ct));

    public Task<RunOnceResponse> RunOnceAsync(
        string instrumentSymbol = "AAPL", string instrumentExchange = "NASDAQ",
        CancellationToken ct = default)
        => Invoke(
            nameof(RunOnceAsync), $"{instrumentSymbol}@{instrumentExchange}",
            () => inner.RunOnceAsync(instrumentSymbol, instrumentExchange, ct));

    public Task<AgentRunResponse> RunAgentAsync(
        string instrumentSymbol = "AAPL", string instrumentExchange = "NASDAQ",
        CancellationToken ct = default)
        => Invoke(
            nameof(RunAgentAsync), $"{instrumentSymbol}@{instrumentExchange}",
            () => inner.RunAgentAsync(instrumentSymbol, instrumentExchange, ct));

    public Task<List<RecommendationDto>> GetRecommendationsAsync(
        string? status = null, int limit = 100, CancellationToken ct = default)
        => Invoke(
            nameof(GetRecommendationsAsync), $"status={status ?? "-"} limit={limit}",
            () => inner.GetRecommendationsAsync(status, limit, ct));

    public Task<ApproveResponse> ApproveRecommendationAsync(
        string recId, ApproveRequest? payload, CancellationToken ct = default)
        => Invoke(
            nameof(ApproveRecommendationAsync), $"recId={recId}",
            () => inner.ApproveRecommendationAsync(recId, payload, ct));

    public Task<Dictionary<string, object?>> RejectRecommendationAsync(
        string recId, RejectRequest? payload, CancellationToken ct = default)
        => Invoke(
            nameof(RejectRecommendationAsync), $"recId={recId}",
            () => inner.RejectRecommendationAsync(recId, payload, ct));

    public Task<AuthorizeUrlResponse> StartSaxoOAuthAsync(
        string accountId, CancellationToken ct = default)
        => Invoke(
            nameof(StartSaxoOAuthAsync), $"accountId={accountId}",
            () => inner.StartSaxoOAuthAsync(accountId, ct));

    public Task<OAuthStatusResponse> GetSaxoOAuthStatusAsync(
        string accountId, CancellationToken ct = default)
        => Invoke(
            nameof(GetSaxoOAuthStatusAsync), $"accountId={accountId}",
            () => inner.GetSaxoOAuthStatusAsync(accountId, ct));

    public Task<AccountInfoResponse> GetAccountAsync(
        string accountId, CancellationToken ct = default)
        => Invoke(
            nameof(GetAccountAsync), $"accountId={accountId}",
            () => inner.GetAccountAsync(accountId, ct));

    public Task<CreateAccountResponse> CreateAccountAsync(
        CreateAccountRequest payload, CancellationToken ct = default)
        => Invoke(
            nameof(CreateAccountAsync), $"name={payload.Name}",
            () => inner.CreateAccountAsync(payload, ct));

    public Task<PlaceOrderResponse> PlaceOrderAsync(
        PlaceOrderRequest payload, CancellationToken ct = default)
        => Invoke(
            nameof(PlaceOrderAsync),
            $"account={payload.AccountId} {payload.Side} {payload.Quantity} "
                + $"{payload.InstrumentSymbol}@{payload.InstrumentExchange}",
            () => inner.PlaceOrderAsync(payload, ct));

    private async Task<T> Invoke<T>(string operation, string? args, Func<Task<T>> call)
    {
        LogEngineCall(logger, operation, args ?? "-");
        try
        {
            return await call().ConfigureAwait(false);
        }
        catch (ApiException ex)
        {
            LogEngineCallFailed(logger, operation, (int)ex.StatusCode, ex.Content ?? string.Empty);
            throw;
        }
    }

    [LoggerMessage(
        EventId = 1,
        Level = LogLevel.Information,
        Message = "engine_call {Operation} args={Args}")]
    private static partial void LogEngineCall(ILogger logger, string operation, string args);

    [LoggerMessage(
        EventId = 2,
        Level = LogLevel.Warning,
        Message = "engine_call_failed {Operation} status={Status} body={Body}")]
    private static partial void LogEngineCallFailed(
        ILogger logger, string operation, int status, string body);
}
