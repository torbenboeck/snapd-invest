using System.Globalization;
using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class RunOnceCommand(IEngineApi api, CancellationTokenSource cts)
    : AsyncCommand<RunOnceCommand.Settings>
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
            var result = await api.RunOnceAsync(settings.Symbol, settings.Exchange, cts.Token);

            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[bold]Strategy:[/] {Markup.Escape(result.Strategy)}  [grey](corr={Markup.Escape(result.CorrelationId)})[/]");
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[bold]Signals:[/] {result.Signals.Count}");

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
                    Markup.Escape($"{s.InstrumentSymbol}@{s.InstrumentExchange}"),
                    $"[{actionColor}]{Markup.Escape(s.Action)}[/]",
                    $"{s.Quantity:N4}",
                    $"{s.Conviction:N2}",
                    Markup.Escape(s.Rationale));
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
                        CultureInfo.InvariantCulture,
                        $"  [red]{Markup.Escape(instrument)}[/]: blocked by risk gate ({Markup.Escape(reason ?? "?")})");
                }
                else
                {
                    AnsiConsole.MarkupLineInterpolated(
                        CultureInfo.InvariantCulture,
                        $"  [green]{Markup.Escape(instrument)}[/]: {Markup.Escape(status)}");
                }
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
