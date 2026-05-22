using System.ComponentModel;
using System.Globalization;
using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class AuthSaxoCommand(IEngineApi api, IBrowserOpener browser, CancellationTokenSource cts)
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

        var ct = cts.Token;
        try
        {
            // Snapshot the existing tokens' timestamp BEFORE starting a new
            // OAuth flow. An "authenticated=true" result alone is a false
            // positive when old (failed-to-refresh) tokens linger in the
            // engine DB — we wait for `TokensUpdatedAt` to advance, which
            // only happens when a NEW callback successfully stores tokens.
            var before = await api.GetSaxoOAuthStatusAsync(settings.AccountId, ct);
            var initialUpdatedAt = before.TokensUpdatedAt;

            var start = await api.StartSaxoOAuthAsync(settings.AccountId, ct);
            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[grey]Opening browser to:[/] {Markup.Escape(start.AuthorizeUrl)}");
            await browser.OpenAsync(start.AuthorizeUrl, ct);

            for (var i = 0; i < settings.MaxAttempts; i++)
            {
                ct.ThrowIfCancellationRequested();
                var status = await api.GetSaxoOAuthStatusAsync(settings.AccountId, ct);
                if (status.Authenticated && status.TokensUpdatedAt != initialUpdatedAt)
                {
                    AnsiConsole.MarkupLine("[green]Tokens stored.[/]");
                    return 0;
                }
                await Task.Delay(settings.PollIntervalMs, ct);
            }

            AnsiConsole.MarkupLine("[red]Timed out waiting for OAuth consent.[/]");
            return 1;
        }
        catch (OperationCanceledException)
        {
            AnsiConsole.MarkupLine("[yellow]Cancelled.[/]");
            return 130;
        }
        catch (Exception ex)
        {
            CliErrors.Render(ex);
            return 1;
        }
    }
}
