using SnapdInvest.Cli.Commands;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Tests.Unit.Commands;

public sealed class AuthSaxoCommandTests
{
    [Fact]
    public async Task ExecuteAsync_OpensBrowser_ThenPollsUntilAuthenticated()
    {
        var api = Substitute.For<IEngineApi>();
        api.StartSaxoOAuthAsync("sim-account", Arg.Any<CancellationToken>())
            .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
        api.GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>())
            .Returns(
                new OAuthStatusResponse("sim-account", "saxo", false),
                new OAuthStatusResponse("sim-account", "saxo", true));

        var browserOpener = Substitute.For<IBrowserOpener>();
        var cmd = new AuthSaxoCommand(api, browserOpener, new CancellationTokenSource());
        var settings = new AuthSaxoCommand.Settings { AccountId = "sim-account", PollIntervalMs = 1 };
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "auth saxo", null);

        var exitCode = await cmd.ExecuteAsync(context, settings);

        exitCode.ShouldBe(0);
        await browserOpener.Received(1)
            .OpenAsync("https://sim.logonvalidation.net/authorize?...", Arg.Any<CancellationToken>());
        await api.Received(2).GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>());
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
