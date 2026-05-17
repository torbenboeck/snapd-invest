using System.Globalization;
using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class StatusCommand(IEngineApi api, CancellationTokenSource cts)
    : AsyncCommand<StatusCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--account")]
        public string AccountName { get; init; } = "paper";
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        var ct = cts.Token;
        try
        {
            var portfolio = await api.GetPortfolioAsync(settings.AccountName, ct);
            var pendingRecos = await api.GetRecommendationsAsync(status: "pending", limit: 10, ct: ct);

            var summaryPanel = new Panel(
                $"[bold]Account:[/] {Markup.Escape(portfolio.AccountName)} ({Markup.Escape(portfolio.BaseCurrency)})\n" +
                $"[bold]Cash:[/]    {portfolio.Cash:N2}\n" +
                $"[bold]Equity:[/]  {(portfolio.Equity?.ToString("N2", CultureInfo.InvariantCulture) ?? "[grey]n/a[/]")}")
            {
                Header = new PanelHeader(" Portfolio "),
                Border = BoxBorder.Rounded,
            };
            AnsiConsole.Write(summaryPanel);

            if (portfolio.Positions.Count > 0)
            {
                var table = new Table()
                    .Title(" Positions ")
                    .Border(TableBorder.Rounded)
                    .AddColumn("Instrument")
                    .AddColumn(new TableColumn("Qty").RightAligned())
                    .AddColumn(new TableColumn("AvgCost").RightAligned())
                    .AddColumn(new TableColumn("Last").RightAligned())
                    .AddColumn(new TableColumn("MV").RightAligned())
                    .AddColumn(new TableColumn("uPnL").RightAligned())
                    .AddColumn("Tag");

                foreach (var p in portfolio.Positions)
                {
                    var pnlColor = (p.UnrealizedPnl ?? 0) >= 0 ? "green" : "red";
                    table.AddRow(
                        Markup.Escape($"{p.InstrumentSymbol}@{p.InstrumentExchange}"),
                        $"{p.Quantity:N4}",
                        $"{p.AvgCost:N2}",
                        p.LastPrice?.ToString("N2", CultureInfo.InvariantCulture) ?? "[grey]?[/]",
                        p.MarketValue?.ToString("N2", CultureInfo.InvariantCulture) ?? "[grey]?[/]",
                        $"[{pnlColor}]{p.UnrealizedPnl?.ToString("N2", CultureInfo.InvariantCulture) ?? "?"}[/]",
                        Markup.Escape(p.Tag));
                }

                AnsiConsole.Write(table);
            }
            else
            {
                AnsiConsole.MarkupLine("[grey]No open positions.[/]");
            }

            if (pendingRecos.Count > 0)
            {
                AnsiConsole.MarkupLine($"\n[yellow]{pendingRecos.Count} pending recommendation(s).[/] Run [cyan]snapdinvest recos[/] to inspect.");
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
