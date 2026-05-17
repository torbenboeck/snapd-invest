using System.Globalization;
using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class RecosCommand(IEngineApi api, CancellationTokenSource cts)
    : AsyncCommand<RecosCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--status")]
        public string Status { get; init; } = "pending";

        [CommandOption("--limit")]
        public int Limit { get; init; } = 20;
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        try
        {
            var status = settings.Status.Equals("all", StringComparison.OrdinalIgnoreCase)
                ? null
                : settings.Status;
            var recos = await api.GetRecommendationsAsync(status, settings.Limit, cts.Token);

            if (recos.Count == 0)
            {
                AnsiConsole.MarkupLineInterpolated(
                    CultureInfo.InvariantCulture,
                    $"[grey]No recommendations with status='{settings.Status}'.[/]");
                return 0;
            }

            foreach (var r in recos)
            {
                var statusColor = r.Status switch
                {
                    "pending" => "yellow",
                    "approved" or "executed" or "modified" => "green",
                    "rejected" or "expired" => "grey",
                    _ => "white",
                };
                var header = $" [{statusColor}]{Markup.Escape(r.Status)}[/]  {Markup.Escape(r.Id)}  [grey](agent={Markup.Escape(r.AgentId)})[/] ";
                var body = $"[bold]Rationale:[/]\n{Markup.Escape(r.Rationale)}\n\n[bold]Signals:[/]\n{Markup.Escape(r.Signals)}";
                var panel = new Panel(body) { Header = new PanelHeader(header), Border = BoxBorder.Rounded };
                AnsiConsole.Write(panel);
            }
            return 0;
        }
        catch (Exception ex)
        {
            CliErrors.Render(ex);
            return 1;
        }
    }
}
