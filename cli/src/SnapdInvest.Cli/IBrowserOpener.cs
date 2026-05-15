using System.Diagnostics;

namespace SnapdInvest.Cli;

public interface IBrowserOpener
{
    Task OpenAsync(string url, CancellationToken ct = default);
}

public sealed class DefaultBrowserOpener : IBrowserOpener
{
    public Task OpenAsync(string url, CancellationToken ct = default)
    {
        var psi = new ProcessStartInfo
        {
            FileName = url,
            UseShellExecute = true,
        };
        Process.Start(psi);
        return Task.CompletedTask;
    }
}
