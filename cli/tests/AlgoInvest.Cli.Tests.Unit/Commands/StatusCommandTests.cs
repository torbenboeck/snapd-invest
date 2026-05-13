using AlgoInvest.Cli.Commands;
using AlgoInvest.Client;
using AlgoInvest.Client.Models;
using Spectre.Console.Cli;

namespace AlgoInvest.Cli.Tests.Unit.Commands;

public sealed class StatusCommandTests
{
    [Fact]
    public async Task ExecuteAsync_ApiSucceeds_ReturnsZero()
    {
        var api = Substitute.For<IEngineApi>();
        api.GetPortfolioAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns(new PortfolioResponse(
                "acc-1", "paper", "DKK", 100_000m, 100_000m, []));
        api.GetRecommendationsAsync(Arg.Any<string?>(), Arg.Any<int>(), Arg.Any<CancellationToken>())
            .Returns([]);

        var command = new StatusCommand(api);
        var result = await command.ExecuteAsync(
            new CommandContext([], Substitute.For<IRemainingArguments>(), "status", null),
            new StatusCommand.Settings());

        result.ShouldBe(0);
    }

    [Fact]
    public async Task ExecuteAsync_ApiThrows_ReturnsOne()
    {
        var api = Substitute.For<IEngineApi>();
        api.GetPortfolioAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
            .Returns<Task<PortfolioResponse>>(_ => throw new HttpRequestException("boom"));

        var command = new StatusCommand(api);
        var result = await command.ExecuteAsync(
            new CommandContext([], Substitute.For<IRemainingArguments>(), "status", null),
            new StatusCommand.Settings());

        result.ShouldBe(1);
    }
}
