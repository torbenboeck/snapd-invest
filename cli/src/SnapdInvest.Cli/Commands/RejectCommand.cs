using System.Globalization;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Commands;

public sealed class RejectCommand(IEngineApi api) : AsyncCommand<RejectCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandArgument(0, "<id>")]
        public string RecommendationId { get; init; } = string.Empty;

        [CommandOption("--reason")]
        public string? Reason { get; init; }
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        try
        {
            await api.RejectRecommendationAsync(
                settings.RecommendationId,
                new RejectRequest(settings.Reason));

            AnsiConsole.MarkupLineInterpolated(
                CultureInfo.InvariantCulture,
                $"[grey]Rejected:[/] {settings.RecommendationId}");
            return 0;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated(CultureInfo.InvariantCulture, $"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }
}
