using SnapdInvest.Cli.Commands;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Tests.Unit.Commands;

public sealed class CreateAccountCommandTests
{
    [Fact]
    public async Task ExecuteAsync_CreatesSimAccountAndPrintsId()
    {
        var api = Substitute.For<IEngineApi>();
        api.CreateAccountAsync(Arg.Any<CreateAccountRequest>(), Arg.Any<CancellationToken>())
            .Returns(new CreateAccountResponse("uuid-1", "saxo-sim", "sim", "DKK", "22264911"));

        var cmd = new CreateAccountCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "create-account", null);
        var settings = new CreateAccountCommand.Settings
        {
            Name = "saxo-sim",
            AccountType = "sim",
            SaxoAccountId = "22264911",
        };

        var exit = await cmd.ExecuteAsync(ctx, settings);

        exit.ShouldBe(0);
        await api.Received(1).CreateAccountAsync(
            Arg.Is<CreateAccountRequest>(r =>
                r.Name == "saxo-sim" &&
                r.AccountType == "sim" &&
                r.SaxoAccountId == "22264911"),
            Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RejectsEmptyName()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new CreateAccountCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "create-account", null);

        var exit = await cmd.ExecuteAsync(ctx, new CreateAccountCommand.Settings { Name = "" });

        exit.ShouldBe(1);
        await api.DidNotReceive().CreateAccountAsync(
            Arg.Any<CreateAccountRequest>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RejectsInvalidType()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new CreateAccountCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "create-account", null);

        var exit = await cmd.ExecuteAsync(
            ctx,
            new CreateAccountCommand.Settings { Name = "x", AccountType = "live" });

        exit.ShouldBe(1);
        await api.DidNotReceive().CreateAccountAsync(
            Arg.Any<CreateAccountRequest>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_HandlesApiError()
    {
        var api = Substitute.For<IEngineApi>();
        api.CreateAccountAsync(Arg.Any<CreateAccountRequest>(), Arg.Any<CancellationToken>())
            .Returns<Task<CreateAccountResponse>>(_ => throw new HttpRequestException("boom"));
        var cmd = new CreateAccountCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "create-account", null);

        var exit = await cmd.ExecuteAsync(
            ctx, new CreateAccountCommand.Settings { Name = "x", AccountType = "paper" });

        exit.ShouldBe(1);
    }
}
