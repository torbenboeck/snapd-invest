namespace SnapdInvest.Client.Models;

// Property naming follows the global PropertyNamingPolicy.SnakeCaseLower
// configured on the Refit serializer in Program.cs. Override per-property
// only when the C# name diverges from the snake_case form.

public sealed record AuthorizeUrlResponse(
    string AuthorizeUrl,
    string State
);

public sealed record OAuthStatusResponse(
    string AccountId,
    string Broker,
    bool Authenticated,
    DateTimeOffset? TokensUpdatedAt = null
);

public sealed record AccountInfoResponse(
    string AccountId,
    string AccountType,
    string? ClientKey,
    string? UserKey,
    string? Name
);

public sealed record CreateAccountRequest(
    string Name,
    string AccountType,
    string BaseCurrency = "DKK",
    decimal InitialCash = 0,
    string? SaxoClientKey = null,
    string? SaxoAccountKey = null,
    string? SaxoAccountId = null
);

public sealed record CreateAccountResponse(
    string AccountId,
    string Name,
    string AccountType,
    string BaseCurrency,
    string? SaxoAccountId
);
