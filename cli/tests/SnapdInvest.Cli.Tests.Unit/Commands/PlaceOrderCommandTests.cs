using System.Net;
using Refit;
using SnapdInvest.Cli.Commands;
using SnapdInvest.Client;
using SnapdInvest.Client.Models;
using Spectre.Console.Cli;

namespace SnapdInvest.Cli.Tests.Unit.Commands;

public sealed class PlaceOrderCommandTests
{
    [Fact]
    public async Task ExecuteAsync_PostsMarketOrderAndReturnsZeroOnFilled()
    {
        var api = Substitute.For<IEngineApi>();
        api.PlaceOrderAsync(Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>())
            .Returns(new PlaceOrderResponse("filled", "5038292933", null, null));

        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);
        var settings = new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "EURDKK@FX",
            Side = "buy",
            Quantity = 1000m,
            OrderType = "market",
        };

        var exit = await cmd.ExecuteAsync(ctx, settings);

        exit.ShouldBe(0);
        await api.Received(1).PlaceOrderAsync(
            Arg.Is<PlaceOrderRequest>(r =>
                r.AccountId == "uuid-1" &&
                r.InstrumentSymbol == "EURDKK" &&
                r.InstrumentExchange == "FX" &&
                r.Side == "buy" &&
                r.Quantity == 1000m &&
                r.LimitPrice == null),
            Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_PostsLimitOrderWithLimitPrice()
    {
        var api = Substitute.For<IEngineApi>();
        api.PlaceOrderAsync(Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>())
            .Returns(new PlaceOrderResponse("filled", "5038292934", null, null));

        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);
        var settings = new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "AAPL@NASDAQ",
            Side = "sell",
            Quantity = 5m,
            OrderType = "limit",
            LimitPrice = 200.50m,
        };

        var exit = await cmd.ExecuteAsync(ctx, settings);

        exit.ShouldBe(0);
        await api.Received(1).PlaceOrderAsync(
            Arg.Is<PlaceOrderRequest>(r =>
                r.LimitPrice == 200.50m && r.Side == "sell" && r.Quantity == 5m),
            Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RejectsMissingAccount()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);

        var exit = await cmd.ExecuteAsync(ctx, new PlaceOrderCommand.Settings
        {
            AccountId = "",
            Symbol = "EURDKK@FX",
            Side = "buy",
            Quantity = 1m,
        });

        exit.ShouldBe(1);
        await api.DidNotReceive().PlaceOrderAsync(
            Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RejectsInvalidSide()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);

        var exit = await cmd.ExecuteAsync(ctx, new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "EURDKK@FX",
            Side = "long",
            Quantity = 1m,
        });

        exit.ShouldBe(1);
        await api.DidNotReceive().PlaceOrderAsync(
            Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RejectsBadSymbolFormat()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);

        var exit = await cmd.ExecuteAsync(ctx, new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "EURDKK",
            Side = "buy",
            Quantity = 1m,
        });

        exit.ShouldBe(1);
        await api.DidNotReceive().PlaceOrderAsync(
            Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_RequiresLimitPriceForLimitOrders()
    {
        var api = Substitute.For<IEngineApi>();
        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);

        var exit = await cmd.ExecuteAsync(ctx, new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "EURDKK@FX",
            Side = "buy",
            Quantity = 1m,
            OrderType = "limit",
            LimitPrice = null,
        });

        exit.ShouldBe(1);
        await api.DidNotReceive().PlaceOrderAsync(
            Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task ExecuteAsync_ReturnsTwoWhenOrderRejected()
    {
        var api = Substitute.For<IEngineApi>();
        api.PlaceOrderAsync(Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>())
            .Returns(new PlaceOrderResponse("rejected", null, "market closed", "MarketClosed"));

        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);
        var settings = new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "EURDKK@FX",
            Side = "buy",
            Quantity = 1m,
        };

        var exit = await cmd.ExecuteAsync(ctx, settings);

        // Non-zero but distinct from validation errors (1) so scripts can branch.
        exit.ShouldBe(2);
    }

    [Fact]
    public async Task ExecuteAsync_HandlesSaxoReauthRequired401()
    {
        var api = Substitute.For<IEngineApi>();
        var apiException = await ApiException.Create(
            new HttpRequestMessage(HttpMethod.Post, "/v1/orders"),
            HttpMethod.Post,
            new HttpResponseMessage(HttpStatusCode.Unauthorized)
            {
                Content = new StringContent(
                    "{\"detail\":{\"code\":\"saxo_reauth_required\",\"account_id\":\"u\",\"message\":\"x\"}}"),
            },
            new RefitSettings());
        api.PlaceOrderAsync(Arg.Any<PlaceOrderRequest>(), Arg.Any<CancellationToken>())
            .Returns<Task<PlaceOrderResponse>>(_ => throw apiException);

        var cmd = new PlaceOrderCommand(api);
        var ctx = new CommandContext([], Substitute.For<IRemainingArguments>(), "place-order", null);
        var settings = new PlaceOrderCommand.Settings
        {
            AccountId = "uuid-1",
            Symbol = "EURDKK@FX",
            Side = "buy",
            Quantity = 1m,
        };

        var exit = await cmd.ExecuteAsync(ctx, settings);

        exit.ShouldBe(1);
    }
}
