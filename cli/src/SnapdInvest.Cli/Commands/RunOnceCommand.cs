using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class RunOnceCommand(IEngineApi api) : AsyncCommand<RunOnceCommand.Settings>
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
            var result = await api.RunOnceAsync(settings.Symbol, settings.Exchange);

            AnsiConsole.MarkupLineInterpolated(
                $"[bold]Strategy:[/] {result.Strategy}  [grey](corr={result.CorrelationId})[/]");
            AnsiConsole.MarkupLineInterpolated($"[bold]Signals:[/] {result.Signals.Count}");

            if (result.Signals.Count == 0)
            {
                AnsiConsole.MarkupLine("[grey]No signal at this tick — strategy was a no-op.[/]");
                return 0;
            }

            var table = new Table()
                .Border(TableBorder.Rounded)
                .AddColumn("Instrument")
                .AddColumn("Action")
                .AddColumn(new TableColumn("Qty").RightAligned())
                .AddColumn(new TableColumn("Conv").RightAligned())
                .AddColumn("Rationale");

            foreach (var s in result.Signals)
            {
                var actionColor = s.Action switch
                {
                    "buy" => "green",
                    "sell" => "red",
                    _ => "yellow"
                };
                table.AddRow(
                    $"{s.InstrumentSymbol}@{s.InstrumentExchange}",
                    $"[{actionColor}]{s.Action}[/]",
                    $"{s.Quantity:N4}",
                    $"{s.Conviction:N2}",
                    s.Rationale);
            }

            AnsiConsole.Write(table);

            AnsiConsole.MarkupLine("\n[bold]Outcomes:[/]");
            foreach (var o in result.Outcomes)
            {
                var status = o.TryGetValue("order_status", out var s) ? s?.ToString() ?? "?" : "?";
                var instrument = o.TryGetValue("instrument", out var i) ? i?.ToString() ?? "?" : "?";
                var allowed = o.TryGetValue("gate_allowed", out var g) && g is true;
                var reason = o.TryGetValue("gate_reason", out var r) ? r?.ToString() : null;

                if (!allowed)
                {
                    AnsiConsole.MarkupLineInterpolated(
                        $"  [red]{instrument}[/]: blocked by risk gate ({reason})");
                }
                else
                {
                    AnsiConsole.MarkupLineInterpolated($"  [green]{instrument}[/]: {status}");
                }
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
