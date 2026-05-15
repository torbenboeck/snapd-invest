using System.ComponentModel;
using System.Globalization;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class CreateAccountCommand(IEngineApi api) : AsyncCommand<CreateAccountCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--name")]
        [Description("Local human label for this account (must be unique)")]
        public string Name { get; init; } = string.Empty;

        [CommandOption("--type")]
        [Description("paper or sim")]
        public string AccountType { get; init; } = "paper";

        [CommandOption("--currency")]
        [Description("Base currency (default DKK)")]
        public string BaseCurrency { get; init; } = "DKK";

        [CommandOption("--initial-cash")]
        [Description("Starting cash for paper accounts")]
        public decimal InitialCash { get; init; }

        [CommandOption("--saxo-account-id")]
        [Description("Human-readable Saxo account number (e.g. '22264911'). Sim only.")]
        public string? SaxoAccountId { get; init; }

        [CommandOption("--saxo-client-key")]
        [Description("Saxo ClientKey (opaque). Sim only. Optional — usually backfilled later.")]
        public string? SaxoClientKey { get; init; }

        [CommandOption("--saxo-account-key")]
        [Description("Saxo AccountKey (opaque). Sim only. Required for T-001-B order placement.")]
        public string? SaxoAccountKey { get; init; }
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.Name))
        {
            AnsiConsole.MarkupLine("[red]--name is required[/]");
            return 1;
        }
        if (settings.AccountType is not ("paper" or "sim"))
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[red]--type must be 'paper' or 'sim', got '{settings.AccountType}'[/]");
            return 1;
        }

        try
        {
            var resp = await api.CreateAccountAsync(new CreateAccountRequest(
                Name: settings.Name,
                AccountType: settings.AccountType,
                BaseCurrency: settings.BaseCurrency,
                InitialCash: settings.InitialCash,
                SaxoClientKey: settings.SaxoClientKey,
                SaxoAccountKey: settings.SaxoAccountKey,
                SaxoAccountId: settings.SaxoAccountId));

            var table = new Table().AddColumn("Field").AddColumn("Value");
            table.AddRow("account_id", resp.AccountId);
            table.AddRow("name", resp.Name);
            table.AddRow("account_type", resp.AccountType);
            table.AddRow("base_currency", resp.BaseCurrency);
            table.AddRow("saxo_account_id", resp.SaxoAccountId ?? "—");
            AnsiConsole.Write(table);
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"\n[green]Created.[/] Use [cyan]--account {resp.AccountId}[/] for subsequent commands.");
            return 0;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture, $"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }
}
