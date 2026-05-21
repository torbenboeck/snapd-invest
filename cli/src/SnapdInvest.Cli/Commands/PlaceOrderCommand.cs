using System.ComponentModel;
using System.Globalization;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class PlaceOrderCommand(IEngineApi api) : AsyncCommand<PlaceOrderCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--account")]
        [Description("Account id (uuid) to place the order against")]
        public string AccountId { get; init; } = string.Empty;

        [CommandOption("--symbol")]
        [Description("Instrument as SYMBOL@EXCHANGE (e.g. EURDKK@FX, AAPL@NASDAQ)")]
        public string Symbol { get; init; } = string.Empty;

        [CommandOption("--side")]
        [Description("buy or sell")]
        public string Side { get; init; } = string.Empty;

        [CommandOption("--qty")]
        [Description("Quantity (FxSpot: base-currency amount; stocks: share count)")]
        public decimal Quantity { get; init; }

        [CommandOption("--type")]
        [Description("market or limit (default market)")]
        public string OrderType { get; init; } = "market";

        [CommandOption("--limit-price")]
        [Description("Limit price (required for --type limit)")]
        public decimal? LimitPrice { get; init; }

        [CommandOption("--source")]
        [Description("Source tag recorded with the order (default manual-cli)")]
        public string Source { get; init; } = "manual-cli";
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.AccountId))
        {
            AnsiConsole.MarkupLine("[red]--account is required[/]");
            return 1;
        }
        if (settings.Side is not ("buy" or "sell"))
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[red]--side must be 'buy' or 'sell', got '{settings.Side}'[/]");
            return 1;
        }
        if (settings.Quantity <= 0)
        {
            AnsiConsole.MarkupLine("[red]--qty must be > 0[/]");
            return 1;
        }
        if (settings.OrderType is not ("market" or "limit"))
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[red]--type must be 'market' or 'limit', got '{settings.OrderType}'[/]");
            return 1;
        }
        if (settings.OrderType == "limit" && settings.LimitPrice is null)
        {
            AnsiConsole.MarkupLine("[red]--limit-price is required when --type=limit[/]");
            return 1;
        }

        var (symbol, exchange) = ParseSymbolAtExchange(settings.Symbol);
        if (symbol is null || exchange is null)
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[red]--symbol must be SYMBOL@EXCHANGE (e.g. EURDKK@FX), got '{settings.Symbol}'[/]");
            return 1;
        }

        try
        {
            var resp = await api.PlaceOrderAsync(new PlaceOrderRequest(
                AccountId: settings.AccountId,
                InstrumentSymbol: symbol,
                InstrumentExchange: exchange,
                Side: settings.Side,
                Quantity: settings.Quantity,
                LimitPrice: settings.OrderType == "limit" ? settings.LimitPrice : null,
                Source: settings.Source));

            var table = new Table().AddColumn("Field").AddColumn("Value");
            table.AddRow("kind", resp.Kind);
            table.AddRow("order_id", resp.OrderId ?? "—");
            table.AddRow("reason", resp.Reason ?? "—");
            table.AddRow("saxo_error_code", resp.SaxoErrorCode ?? "—");
            AnsiConsole.Write(table);

            return resp.Kind switch
            {
                "filled" or "idempotent_replay" => 0,
                _ => 2,
            };
        }
        catch (Refit.ApiException ex) when ((int)ex.StatusCode == 401)
        {
            AnsiConsole.MarkupLine("[red]Saxo session expired or never authenticated.[/]");
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"Run: [cyan]snapdinvest auth saxo --account {settings.AccountId}[/]");
            return 1;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture, $"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }

    private static (string? symbol, string? exchange) ParseSymbolAtExchange(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return (null, null);
        }
        var at = raw.IndexOf('@');
        if (at <= 0 || at == raw.Length - 1)
        {
            return (null, null);
        }
        return (raw[..at], raw[(at + 1)..]);
    }
}
