using SnapdInvest.Client;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class AuditCommand(IEngineApi api, CancellationTokenSource cts)
    : AsyncCommand<AuditCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandOption("--limit")]
        public int Limit { get; init; } = 20;

        [CommandOption("--type")]
        public string? Type { get; init; }
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        try
        {
            var events = await api.GetAuditAsync(settings.Limit, settings.Type, cts.Token);

            if (events.Count == 0)
            {
                AnsiConsole.MarkupLine("[grey]No audit events.[/]");
                return 0;
            }

            var table = new Table()
                .Border(TableBorder.Rounded)
                .AddColumn("When")
                .AddColumn("Type")
                .AddColumn("Correlation")
                .AddColumn("Payload");

            foreach (var e in events)
            {
                table.AddRow(
                    Markup.Escape(e.OccurredAt),
                    $"[cyan]{Markup.Escape(e.Type)}[/]",
                    e.CorrelationId is null ? "[grey]-[/]" : Markup.Escape(e.CorrelationId),
                    Markup.Escape(Truncate(e.Payload, 80)));
            }

            AnsiConsole.Write(table);
            return 0;
        }
        catch (Exception ex)
        {
            CliErrors.Render(ex);
            return 1;
        }
    }

    private static string Truncate(string s, int max)
        => s.Length <= max ? s : s[..max] + "...";
}
