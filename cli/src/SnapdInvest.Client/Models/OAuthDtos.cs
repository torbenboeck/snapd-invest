using System.Text.Json.Serialization;

namespace SnapdInvest.Client.Models;

public sealed record AuthorizeUrlResponse(
    [property: JsonPropertyName("authorize_url")] string AuthorizeUrl,
    string State
);

public sealed record OAuthStatusResponse(
    [property: JsonPropertyName("account_id")] string AccountId,
    string Broker,
    bool Authenticated
);

public sealed record AccountInfoResponse(
    [property: JsonPropertyName("account_id")] string AccountId,
    [property: JsonPropertyName("account_type")] string AccountType,
    [property: JsonPropertyName("client_key")] string? ClientKey,
    [property: JsonPropertyName("user_key")] string? UserKey,
    string? Name
);

public sealed record CreateAccountRequest(
    string Name,
    [property: JsonPropertyName("account_type")] string AccountType,
    [property: JsonPropertyName("base_currency")] string BaseCurrency = "DKK",
    [property: JsonPropertyName("initial_cash")] decimal InitialCash = 0,
    [property: JsonPropertyName("saxo_client_key")] string? SaxoClientKey = null,
    [property: JsonPropertyName("saxo_account_key")] string? SaxoAccountKey = null,
    [property: JsonPropertyName("saxo_account_id")] string? SaxoAccountId = null
);

public sealed record CreateAccountResponse(
    [property: JsonPropertyName("account_id")] string AccountId,
    string Name,
    [property: JsonPropertyName("account_type")] string AccountType,
    [property: JsonPropertyName("base_currency")] string BaseCurrency,
    [property: JsonPropertyName("saxo_account_id")] string? SaxoAccountId
);
