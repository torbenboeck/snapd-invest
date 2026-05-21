using System.Globalization;
using Refit;
using Spectre.Console;

namespace SnapdInvest.Cli;

internal static class CliErrors
{
    /// Returns the engine's structured error body when available (Refit's
    /// ApiException.Content), falling back to Exception.Message. The engine
    /// emits JSON detail bodies that are much more useful to users than
    /// Refit's generic "Response status code does not indicate success" wrap.
    public static string Format(Exception ex) => ex switch
    {
        ApiException { Content.Length: > 0 } api =>
            string.Create(
                CultureInfo.InvariantCulture,
                $"HTTP {(int)api.StatusCode}: {api.Content}"),
        _ => ex.Message,
    };

    /// Renders `[red]Error:[/] {escaped engine text}` to the console.
    /// `Markup.Escape` ensures `[` / `]` in engine output doesn't corrupt
    /// Spectre markup.
    public static void Render(Exception ex) =>
        AnsiConsole.MarkupLineInterpolated(
            CultureInfo.InvariantCulture,
            $"[red]Error:[/] {Markup.Escape(Format(ex))}");
}
