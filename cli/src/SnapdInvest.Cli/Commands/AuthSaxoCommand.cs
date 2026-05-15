using System.ComponentModel;
using System.Globalization;
using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class AuthSaxoCommand(IEngineApi api, IBrowserOpener browser)
    : AsyncCommand<AuthSaxoCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--account")]
        [Description("Account id to authenticate against Saxo SIM")]
        public string AccountId { get; init; } = string.Empty;

        [CommandOption("--poll-interval-ms")]
        [Description("How often to poll for token presence")]
        public int PollIntervalMs { get; init; } = 1500;

        [CommandOption("--max-attempts")]
        [Description("Give up after this many polls")]
        public int MaxAttempts { get; init; } = 120;
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
            var start = await api.StartSaxoOAuthAsync(settings.AccountId);
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture, $"[grey]Opening browser to:[/] {start.AuthorizeUrl}");
            await browser.OpenAsync(start.AuthorizeUrl);

            for (var i = 0; i < settings.MaxAttempts; i++)
            {
                var status = await api.GetSaxoOAuthStatusAsync(settings.AccountId);
                if (status.Authenticated)
                {
                    AnsiConsole.MarkupLine("[green]Tokens stored.[/]");
                    return 0;
                }
                await Task.Delay(settings.PollIntervalMs);
            }

            AnsiConsole.MarkupLine("[red]Timed out waiting for OAuth consent.[/]");
            return 1;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture, $"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }
}
