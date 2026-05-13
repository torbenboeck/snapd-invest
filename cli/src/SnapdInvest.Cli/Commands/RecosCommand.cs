using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class RecosCommand(IEngineApi api) : AsyncCommand<RecosCommand.Settings>
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
            var recos = await api.GetRecommendationsAsync(status, settings.Limit);

            if (recos.Count == 0)
            {
                AnsiConsole.MarkupLineInterpolated(
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
                var header = $" [{statusColor}]{r.Status}[/]  {r.Id}  [grey](agent={r.AgentId})[/] ";
                var body = $"[bold]Rationale:[/]\n{r.Rationale}\n\n[bold]Signals:[/]\n{r.Signals}";
                var panel = new Panel(body) { Header = new PanelHeader(header), Border = BoxBorder.Rounded };
                AnsiConsole.Write(panel);
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
