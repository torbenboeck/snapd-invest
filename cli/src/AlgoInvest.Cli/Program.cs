using AlgoInvest.Cli;
using AlgoInvest.Cli.Commands;
using AlgoInvest.Client;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Refit;
using Serilog;
using Spectre.Console.Cli;

var configuration = new ConfigurationBuilder()
    .SetBasePath(AppContext.BaseDirectory)
    .AddJsonFile("appsettings.json", optional: true)
    .AddEnvironmentVariables(prefix: "ALGOINVEST_")
    .AddCommandLine(args)
    .Build();

Log.Logger = new LoggerConfiguration()
    .ReadFrom.Configuration(configuration)
    .CreateLogger();

var services = new ServiceCollection();
services.AddSingleton<IConfiguration>(configuration);
services.Configure<EngineOptions>(configuration.GetSection(EngineOptions.SectionName));
services.AddLogging(b => b.AddSerilog(dispose: true));

services
    .AddRefitClient<IEngineApi>()
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
    config.SetApplicationName("algoinvest");
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
});

return await app.RunAsync(args);
