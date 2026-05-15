using System.Text.Json;
using System.Text.Json.Serialization;
using SnapdInvest.Cli;
using SnapdInvest.Cli.Commands;
using SnapdInvest.Client;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Refit;
using Serilog;
using Spectre.Console.Cli;

var configuration = new ConfigurationBuilder()
    .SetBasePath(AppContext.BaseDirectory)
    .AddJsonFile("appsettings.json", optional: true)
    .AddEnvironmentVariables(prefix: "SNAPDINVEST_")
    .AddCommandLine(args)
    .Build();

Log.Logger = new LoggerConfiguration()
    .ReadFrom.Configuration(configuration)
    .CreateLogger();

var services = new ServiceCollection();
services.AddSingleton<IConfiguration>(configuration);
services.Configure<EngineOptions>(configuration.GetSection(EngineOptions.SectionName));
services.AddLogging(b => b.AddSerilog(dispose: true));
services.AddTransient<IBrowserOpener, DefaultBrowserOpener>();

// Engine response shape:
// - All keys are snake_case (FastAPI / Pydantic V2 default + our DTO names).
//   C# DTOs use PascalCase, so we need a snake_case PropertyNamingPolicy.
// - Decimal fields (Cash, Equity, Quantity, AvgCost, …) come back as JSON
//   strings, not numbers — Pydantic V2 serializes Decimal as string to
//   preserve precision (e.g. {"cash": "100000.0000"}). System.Text.Json
//   refuses string→decimal without explicit opt-in via NumberHandling.
//
// Without this configuration, PortfolioResponse / RecommendationDto etc.
// throw "error deserializing the response" in production. Tests didn't
// catch the snake_case issue because they use NSubstitute mocks; the
// JsonSerializationTests added in this branch exercise the actual
// serializer with both number-form and string-form decimals.
var refitSettings = new RefitSettings(
    new SystemTextJsonContentSerializer(new JsonSerializerOptions
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
    }));

services
    .AddRefitClient<IEngineApi>(refitSettings)
    .ConfigureHttpClient((sp, c) =>
    {
        var url = configuration[$"{EngineOptions.SectionName}:Url"] ?? "http://localhost:8000";
        c.BaseAddress = new Uri(url);
        c.Timeout = TimeSpan.FromSeconds(30);
    });

var registrar = new TypeRegistrar(services);
var app = new CommandApp(registrar);

app.Configure(config =>
{
    config.SetApplicationName("snapdinvest");
    config.ValidateExamples();

    config.AddCommand<StatusCommand>("status")
        .WithDescription("Show portfolio summary and pending recommendations.");

    config.AddCommand<RunOnceCommand>("run-once")
        .WithDescription("Trigger the MicroTrader strategy once for an instrument.");

    config.AddCommand<RunAgentCommand>("run-agent")
        .WithDescription("Trigger the configured agent once for an instrument.");

    config.AddCommand<AuditCommand>("audit")
        .WithDescription("Show recent audit events, newest first.");

    config.AddCommand<RecosCommand>("recos")
        .WithDescription("List recommendations (pending by default).");

    config.AddCommand<ApproveCommand>("approve")
        .WithDescription("Approve a pending recommendation, optionally modifying quantities.");

    config.AddCommand<RejectCommand>("reject")
        .WithDescription("Reject a pending recommendation.");

    config.AddBranch("auth", auth =>
    {
        auth.AddCommand<AuthSaxoCommand>("saxo")
            .WithDescription("Authenticate against Saxo SIM via OAuth (PKCE).");
    });

    config.AddCommand<GetAccountCommand>("get-account")
        .WithDescription("Show account details (delegates through the engine to the configured broker).");

    config.AddCommand<CreateAccountCommand>("create-account")
        .WithDescription("Create a new account row in the engine DB. Required before 'auth saxo'.");
});

return await app.RunAsync(args);
