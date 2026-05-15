using SnapdInvest.Cli.Commands;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Tests.Unit.Commands;

public sealed class GetAccountCommandTests
{
    [Fact]
    public async Task ExecuteAsync_PrintsAccountInfo()
    {
        var api = Substitute.For<IEngineApi>();
        api.GetAccountAsync("acc-1", Arg.Any<CancellationToken>())
            .Returns(new AccountInfoResponse("acc-1", "sim", "client-key", "user-key", "Torben"));

        var cmd = new GetAccountCommand(api);
        var settings = new GetAccountCommand.Settings { AccountId = "acc-1" };
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "get-account", null);

        var exitCode = await cmd.ExecuteAsync(context, settings);
        exitCode.ShouldBe(0);
        await api.Received(1).GetAccountAsync("acc-1", Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RejectsEmptyAccount()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new GetAccountCommand(api);
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "get-account", null);

        var exitCode = await cmd.ExecuteAsync(context, new GetAccountCommand.Settings { AccountId = "" });

        exitCode.ShouldBe(1);
        await api.DidNotReceive().GetAccountAsync(Arg.Any<string>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_HandlesApiError()
    {
        var api = Substitute.For<IEngineApi>();
        api.GetAccountAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns<Task<AccountInfoResponse>>(_ => throw new HttpRequestException("boom"));

        var cmd = new GetAccountCommand(api);
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "get-account", null);

        var exitCode = await cmd.ExecuteAsync(context, new GetAccountCommand.Settings { AccountId = "x" });
        exitCode.ShouldBe(1);
    }
}
