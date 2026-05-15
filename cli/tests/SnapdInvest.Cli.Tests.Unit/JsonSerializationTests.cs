using System.Text.Json;
using System.Text.Json.Serialization;
using SnapdInvest.Client.Models;

namespace SnapdInvest.Cli.Tests.Unit;

/// <summary>
/// Smoke tests that real engine-shaped JSON deserializes into the C# DTOs.
/// The rest of the test suite mocks <c>IEngineApi</c> via NSubstitute, so it
/// never exercises Refit's serializer — these tests fill that gap by
/// running JSON taken from the engine's live response shape through the
/// same <see cref="JsonSerializerOptions"/> Program.cs configures on the
/// shared Refit serializer.
///
/// If these break, the production CLI will silently deserialize fields to
/// null/default and tables will render empty.
/// </summary>
public sealed class JsonSerializationTests
{
    private static readonly JsonSerializerOptions EngineJson = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
    };

    [Fact]
    public void PortfolioResponse_DeserializesFromSnakeCase()
    {
        const string json = """
            {
                "account_id": "uuid-1",
                "account_name": "paper",
                "base_currency": "DKK",
                "cash": 100000.0,
                "equity": 102500.0,
                "positions": []
            }
            """;
        var dto = JsonSerializer.Deserialize<PortfolioResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.AccountId.ShouldBe("uuid-1");
        dto.AccountName.ShouldBe("paper");
        dto.BaseCurrency.ShouldBe("DKK");
        dto.Cash.ShouldBe(100000.0m);
        dto.Equity.ShouldBe(102500.0m);
    }

    [Fact]
    public void PortfolioResponse_DeserializesDecimalsFromQuotedStrings()
    {
        // Pydantic V2 serializes Decimal as a JSON string to preserve
        // precision. FastAPI hands back exactly this shape from /v1/portfolio.
        const string json = """
            {
                "account_id": "uuid-1",
                "account_name": "paper",
                "base_currency": "DKK",
                "cash": "100000.0000",
                "equity": "102500.5000",
                "positions": []
            }
            """;
        var dto = JsonSerializer.Deserialize<PortfolioResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.Cash.ShouldBe(100000m);
        dto.Equity.ShouldBe(102500.5m);
    }

    [Fact]
    public void PositionDto_DeserializesDecimalsFromQuotedStrings()
    {
        const string json = """
            {
                "instrument_symbol": "AAPL",
                "instrument_exchange": "NASDAQ",
                "quantity": "10.0000",
                "avg_cost": "150.0000",
                "last_price": "175.0000",
                "market_value": "1750.0000",
                "unrealized_pnl": "250.0000",
                "tag": "managed"
            }
            """;
        var dto = JsonSerializer.Deserialize<PositionDto>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.Quantity.ShouldBe(10m);
        dto.UnrealizedPnl.ShouldBe(250m);
    }

    [Fact]
    public void PositionDto_DeserializesNestedSnakeCaseFields()
    {
        const string json = """
            {
                "instrument_symbol": "AAPL",
                "instrument_exchange": "NASDAQ",
                "quantity": 10,
                "avg_cost": 150.0,
                "last_price": 175.0,
                "market_value": 1750.0,
                "unrealized_pnl": 250.0,
                "tag": "managed"
            }
            """;
        var dto = JsonSerializer.Deserialize<PositionDto>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.InstrumentSymbol.ShouldBe("AAPL");
        dto.InstrumentExchange.ShouldBe("NASDAQ");
        dto.UnrealizedPnl.ShouldBe(250.0m);
    }

    [Fact]
    public void AuditEventDto_DeserializesFromSnakeCase()
    {
        const string json = """
            {
                "id": "evt-1",
                "type": "signal_emitted",
                "payload": "{}",
                "correlation_id": "corr-1",
                "occurred_at": "2026-05-15T10:00:00Z"
            }
            """;
        var dto = JsonSerializer.Deserialize<AuditEventDto>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.CorrelationId.ShouldBe("corr-1");
        dto.OccurredAt.ShouldBe("2026-05-15T10:00:00Z");
    }

    [Fact]
    public void RecommendationDto_DeserializesAllSnakeCaseFields()
    {
        const string json = """
            {
                "id": "rec-1",
                "agent_id": "agent-1",
                "status": "pending",
                "rationale": "buy on golden cross",
                "signals": "[]",
                "correlation_id": null,
                "created_at": "2026-05-15T10:00:00Z",
                "expires_at": "2026-05-15T11:00:00Z",
                "resolved_at": null
            }
            """;
        var dto = JsonSerializer.Deserialize<RecommendationDto>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.AgentId.ShouldBe("agent-1");
        dto.CreatedAt.ShouldBe("2026-05-15T10:00:00Z");
        dto.ResolvedAt.ShouldBeNull();
    }

    [Fact]
    public void OAuthStatusResponse_DeserializesFromSnakeCase()
    {
        const string json = """{"account_id":"uuid-1","broker":"saxo","authenticated":true}""";
        var dto = JsonSerializer.Deserialize<OAuthStatusResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.AccountId.ShouldBe("uuid-1");
        dto.Authenticated.ShouldBeTrue();
    }

    [Fact]
    public void AuthorizeUrlResponse_DeserializesFromSnakeCase()
    {
        const string json = """{"authorize_url":"https://sim.logonvalidation.net/authorize?...","state":"abc"}""";
        var dto = JsonSerializer.Deserialize<AuthorizeUrlResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.AuthorizeUrl.ShouldStartWith("https://sim.logonvalidation.net");
    }

    [Fact]
    public void AccountInfoResponse_DeserializesFromSnakeCase()
    {
        const string json = """
            {
                "account_id": "uuid-1",
                "account_type": "sim",
                "client_key": "ck",
                "user_key": "uk",
                "name": "Torben"
            }
            """;
        var dto = JsonSerializer.Deserialize<AccountInfoResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.AccountType.ShouldBe("sim");
        dto.ClientKey.ShouldBe("ck");
        dto.UserKey.ShouldBe("uk");
    }

    [Fact]
    public void CreateAccountRequest_SerializesToSnakeCase()
    {
        var req = new CreateAccountRequest(
            Name: "saxo-sim",
            AccountType: "sim",
            BaseCurrency: "DKK",
            InitialCash: 0m,
            SaxoAccountId: "22264911");
        var json = JsonSerializer.Serialize(req, EngineJson);
        json.ShouldContain("\"account_type\":\"sim\"");
        json.ShouldContain("\"base_currency\":\"DKK\"");
        json.ShouldContain("\"saxo_account_id\":\"22264911\"");
        json.ShouldContain("\"initial_cash\":0");
    }

    [Fact]
    public void CreateAccountResponse_DeserializesFromSnakeCase()
    {
        const string json = """
            {
                "account_id": "uuid-1",
                "name": "saxo-sim",
                "account_type": "sim",
                "base_currency": "DKK",
                "saxo_account_id": "22264911"
            }
            """;
        var dto = JsonSerializer.Deserialize<CreateAccountResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.AccountId.ShouldBe("uuid-1");
        dto.SaxoAccountId.ShouldBe("22264911");
    }

    [Fact]
    public void RunOnceResponse_DeserializesFromSnakeCase()
    {
        const string json = """
            {
                "correlation_id": "corr-1",
                "strategy": "sma_crossover",
                "signals": [],
                "outcomes": []
            }
            """;
        var dto = JsonSerializer.Deserialize<RunOnceResponse>(json, EngineJson);
        dto.ShouldNotBeNull();
        dto.CorrelationId.ShouldBe("corr-1");
        dto.Strategy.ShouldBe("sma_crossover");
    }
}
