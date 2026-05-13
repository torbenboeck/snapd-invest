using AlgoInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace AlgoInvest.Cli.Commands;

public sealed class RunAgentCommand(IEngineApi api) : AsyncCommand<RunAgentCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--symbol")]
        public string Symbol { get; init; } = "AAPL";

        [CommandOption("--exchange")]
        public string Exchange { get; init; } = "NASDAQ";
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        try
        {
            var result = await AnsiConsole.Status()
                .StartAsync("Running agent (this may take a few seconds)...",
                    async _ => await api.RunAgentAsync(settings.Symbol, settings.Exchange));

            var panel = new Panel(result.Summary)
            {
                Header = new PanelHeader($" Agent: {result.Agent} "),
                Border = BoxBorder.Rounded,
            };
            AnsiConsole.Write(panel);

            if (result.RecommendationId is null)
            {
                AnsiConsole.MarkupLine("[grey]No actionable recommendation produced this run.[/]");
            }
            else
            {
                AnsiConsole.MarkupLineInterpolated(
                    $"[green]Recommendation created:[/] {result.RecommendationId}");
                AnsiConsole.MarkupLine("Run [cyan]algoinvest recos[/] to inspect, then [cyan]algoinvest approve <id>[/] to execute.");
            }

            return 0;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated($"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }
}
