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
