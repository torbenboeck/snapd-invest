using System.ComponentModel;
using System.Globalization;
using System.Net;
using System.Text.Json;
using Refit;
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
        catch (ApiException ex) when (ex.StatusCode == HttpStatusCode.Unauthorized
                                      && IsSaxoReauthRequired(ex))
        {
            // The engine signals "user needs to re-run auth saxo" via
            // {"detail": {"code": "saxo_reauth_required", ...}}. Saxo SIM
            // refresh tokens expire quickly — surface the actionable
            // command rather than a generic 401 stack trace.
            AnsiConsole.MarkupLine("[yellow]Saxo session expired or never authenticated.[/]");
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"Run: [bold]snapdinvest auth saxo --account {settings.AccountId}[/]");
            return 1;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture, $"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }

    private static bool IsSaxoReauthRequired(ApiException ex)
    {
        if (string.IsNullOrEmpty(ex.Content))
        {
            return false;
        }
        try
        {
            using var doc = JsonDocument.Parse(ex.Content);
            if (doc.RootElement.TryGetProperty("detail", out var detail)
                && detail.ValueKind == JsonValueKind.Object
                && detail.TryGetProperty("code", out var code)
                && code.ValueKind == JsonValueKind.String)
            {
                return code.GetString() == "saxo_reauth_required";
            }
        }
        catch (JsonException)
        {
            // Body wasn't JSON — fall through to the generic error path.
        }
        return false;
    }
}
