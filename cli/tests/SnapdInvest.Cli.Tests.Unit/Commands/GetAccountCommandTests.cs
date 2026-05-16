using System.Net;
using Refit;
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

    [Fact]
    public async Task ExecuteAsync_PrintsHelpfulMessage_OnReauthRequired()
    {
        const string accountId = "6f053be7-1ca3-4166-a9dd-61bafc5618e2";
        var apiException = await BuildApiExceptionAsync(
            HttpStatusCode.Unauthorized,
            """{"detail":{"code":"saxo_reauth_required","message":"Saxo session expired","account_id":"6f053be7-1ca3-4166-a9dd-61bafc5618e2"}}""");

        var api = Substitute.For<IEngineApi>();
        api.GetAccountAsync(accountId, Arg.Any<CancellationToken>())
            .Returns<Task<AccountInfoResponse>>(_ => throw apiException);

        var cmd = new GetAccountCommand(api);
        var context = new CommandContext([], Substitute.For<IRemainingArguments>(), "get-account", null);

        var exitCode = await cmd.ExecuteAsync(context, new GetAccountCommand.Settings { AccountId = accountId });
        exitCode.ShouldBe(1);
    }

    private static async Task<ApiException> BuildApiExceptionAsync(HttpStatusCode status, string body)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, "http://test/v1/accounts/x");
        var response = new HttpResponseMessage(status)
        {
            Content = new StringContent(body, System.Text.Encoding.UTF8, "application/json"),
            RequestMessage = request,
        };
        return await ApiException.Create(request, HttpMethod.Get, response, new RefitSettings());
    }
}
