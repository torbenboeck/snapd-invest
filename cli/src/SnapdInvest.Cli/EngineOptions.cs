namespace SnapdInvest.Cli;

public sealed class EngineOptions
{
    public const string SectionName = "Engine";

    public string Url { get; set; } = "http://localhost:8000";
}
