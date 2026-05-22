using SnapdInvest.Cli.Commands;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Tests.Unit.Commands;

public sealed class AuthSaxoCommandTests
{
    [Fact]
    public async Task ExecuteAsync_OpensBrowser_ThenPollsUntilTokensUpdatedAtAdvances()
    {
        var api = Substitute.For<IEngineApi>();
        api.StartSaxoOAuthAsync("sim-account", Arg.Any<CancellationToken>())
            .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
        // Sequence: initial snapshot (stale tokens), one poll showing the
        // same stale value, then a poll where the timestamp has advanced.
        var stale = new DateTimeOffset(2026, 5, 22, 8, 0, 0, TimeSpan.Zero);
        var fresh = new DateTimeOffset(2026, 5, 22, 9, 0, 0, TimeSpan.Zero);
        api.GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>())
            .Returns(
                new OAuthStatusResponse("sim-account", "saxo", true, stale),
                new OAuthStatusResponse("sim-account", "saxo", true, stale),
                new OAuthStatusResponse("sim-account", "saxo", true, fresh));

        var browserOpener = Substitute.For<IBrowserOpener>();
        var cmd = new AuthSaxoCommand(api, browserOpener, new CancellationTokenSource());
        var settings = new AuthSaxoCommand.Settings { AccountId = "sim-account", PollIntervalMs = 1 };
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "auth saxo", null);

        var exitCode = await cmd.ExecuteAsync(context, settings);

        exitCode.ShouldBe(0);
        await browserOpener.Received(1)
            .OpenAsync("https://sim.logonvalidation.net/authorize?...", Arg.Any<CancellationToken>());
        // 1 snapshot before browser + 2 polls (first sees stale, second sees fresh).
        await api.Received(3).GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_IgnoresPreExistingAuthenticatedWithSameTokensUpdatedAt()
    {
        // Regression: the engine reports `authenticated=true` whenever any
        // tokens are stored, including old broken ones. The CLI must wait
        // for `TokensUpdatedAt` to advance, not just for `Authenticated`.
        var api = Substitute.For<IEngineApi>();
        api.StartSaxoOAuthAsync("sim-account", Arg.Any<CancellationToken>())
            .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
        var stale = new DateTimeOffset(2026, 5, 22, 8, 0, 0, TimeSpan.Zero);
        api.GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>())
            .Returns(new OAuthStatusResponse("sim-account", "saxo", true, stale));

        var cmd = new AuthSaxoCommand(api, Substitute.For<IBrowserOpener>(), new CancellationTokenSource());
        var settings = new AuthSaxoCommand.Settings
        {
            AccountId = "sim-account",
            PollIntervalMs = 1,
            MaxAttempts = 3,
        };
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "auth saxo", null);

        var exitCode = await cmd.ExecuteAsync(context, settings);

        exitCode.ShouldBe(1); // timeout — never saw a fresh timestamp.
    }

    [Fact]
    public async Task ExecuteAsync_TimesOutAfterMaxAttempts()
    {
        var api = Substitute.For<IEngineApi>();
        api.StartSaxoOAuthAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
        api.GetSaxoOAuthStatusAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns(new OAuthStatusResponse("sim-account", "saxo", false));

        var browserOpener = Substitute.For<IBrowserOpener>();
        var cmd = new AuthSaxoCommand(api, browserOpener, new CancellationTokenSource());
        var settings = new AuthSaxoCommand.Settings
        {
            AccountId = "sim-account",
            PollIntervalMs = 1,
            MaxAttempts = 3,
        };
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "auth saxo", null);

        var exitCode = await cmd.ExecuteAsync(context, settings);

        exitCode.ShouldBe(1);
    }

    [Fact]
    public async Task ExecuteAsync_RespectsCancellation_DuringPolling()
    {
        var api = Substitute.For<IEngineApi>();
        api.StartSaxoOAuthAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
        api.GetSaxoOAuthStatusAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns(new OAuthStatusResponse("sim-account", "saxo", false));

        var cts = new CancellationTokenSource();
        cts.Cancel(); // already cancelled — first ThrowIfCancellationRequested exits the loop

        var cmd = new AuthSaxoCommand(api, Substitute.For<IBrowserOpener>(), cts);
        var settings = new AuthSaxoCommand.Settings
        {
            AccountId = "sim-account",
            PollIntervalMs = 1,
            MaxAttempts = 100,
        };
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "auth saxo", null);

        var exitCode = await cmd.ExecuteAsync(context, settings);

        exitCode.ShouldBe(130);
    }

    [Fact]
    public async Task ExecuteAsync_RejectsEmptyAccount()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new AuthSaxoCommand(api, Substitute.For<IBrowserOpener>(), new CancellationTokenSource());
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "auth saxo", null);

        var exitCode = await cmd.ExecuteAsync(context, new AuthSaxoCommand.Settings { AccountId = "" });

        exitCode.ShouldBe(1);
        await api.DidNotReceive().StartSaxoOAuthAsync(Arg.Any<string>(), Arg.Any<CancellationToken>());
    }
}
