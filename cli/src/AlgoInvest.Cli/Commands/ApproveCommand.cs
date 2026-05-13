using AlgoInvest.Client;
using AlgoInvest.Client.Models;
using Spectre.Console;
using Spectre.Console.Cli;

namespace AlgoInvest.Cli.Commands;

public sealed class ApproveCommand(IEngineApi api) : AsyncCommand<ApproveCommand.Settings>
{
    public sealed class Settings : CommandSettings
    {
        [CommandArgument(0, "<id>")]
        public string RecommendationId { get; init; } = string.Empty;

        [CommandOption("--yes")]
        public bool SkipConfirm { get; init; }

        // Format: SYMBOL@EXCHANGE=QTY (or SYMBOL@EXCHANGE=skip)
        [CommandOption("--modify <values>")]
        public string[] Modifications { get; init; } = [];
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        try
        {
            var mods = ParseModifications(settings.Modifications);

            if (!settings.SkipConfirm
                && !AnsiConsole.Confirm($"Approve and execute recommendation {settings.RecommendationId}?"))
            {
                AnsiConsole.MarkupLine("[grey]Aborted.[/]");
                return 0;
            }

            var response = await api.ApproveRecommendationAsync(
                settings.RecommendationId,
                new ApproveRequest(mods.Count == 0 ? null : mods));

            AnsiConsole.MarkupLineInterpolated(
                $"[green]Status:[/] {response.Status}  [grey](id={response.RecommendationId})[/]");

            foreach (var o in response.ExecutionSummaries)
            {
                var instrument = o.TryGetValue("instrument", out var i) ? i?.ToString() ?? "?" : "?";
                var status = o.TryGetValue("order_status", out var s) ? s?.ToString() ?? "?" : "?";
                var allowed = o.TryGetValue("gate_allowed", out var g) && g is true;
                if (!allowed)
                {
                    var reason = o.TryGetValue("gate_reason", out var r) ? r?.ToString() : "?";
                    AnsiConsole.MarkupLineInterpolated($"  [red]{instrument}[/]: blocked ({reason})");
                }
                else
                {
                    AnsiConsole.MarkupLineInterpolated($"  [green]{instrument}[/]: {status}");
                }
            }

            return 0;
        }
        catch (Exception ex)
        {
            AnsiConsole.MarkupLineInterpolated($"[red]Error:[/] {ex.Message}");
            return 1;
        }
    }

    private static List<SignalModificationDto> ParseModifications(string[] inputs)
    {
        var result = new List<SignalModificationDto>();
        foreach (var raw in inputs)
        {
            var parts = raw.Split('=', 2);
            if (parts.Length != 2)
            {
                throw new ArgumentException($"Invalid --modify value '{raw}'. Expected SYMBOL@EXCHANGE=qty|skip.");
            }
            var instr = parts[0].Split('@', 2);
            if (instr.Length != 2)
            {
                throw new ArgumentException($"Invalid instrument '{parts[0]}'. Expected SYMBOL@EXCHANGE.");
            }
            var value = parts[1];
            if (value.Equals("skip", StringComparison.OrdinalIgnoreCase))
            {
                result.Add(new SignalModificationDto(instr[0], instr[1], null, true));
            }
            else if (decimal.TryParse(value, System.Globalization.CultureInfo.InvariantCulture, out var qty))
            {
                result.Add(new SignalModificationDto(instr[0], instr[1], qty, false));
            }
            else
            {
                throw new ArgumentException($"Invalid quantity '{value}'. Use a decimal or 'skip'.");
            }
        }
        return result;
    }
}
