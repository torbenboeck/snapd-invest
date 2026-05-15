using System.ComponentModel;
using System.Globalization;
using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class GetAccountCommand(IEngineApi api) : AsyncCommand<GetAccountCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--account")]
        [Description("Account id")]
        public string AccountId { get; init; } = string.Empty;
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.AccountId))
        {
            AnsiConsole.MarkupLine("[red]--account is required[/]");
            return 1;
        }

        try
        {
            var info = await api.GetAccountAsync(settings.AccountId);
            var table = new Table().AddColumn("Field").AddColumn("Value");
            table.AddRow("account_id", info.AccountId);
            table.AddRow("account_type", info.AccountType);
            table.AddRow("client_key", info.ClientKey ?? "—");
            table.AddRow("user_key", info.UserKey ?? "—");
            table.AddRow("name", info.Name ?? "—");
            AnsiConsole.Write(table);
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
