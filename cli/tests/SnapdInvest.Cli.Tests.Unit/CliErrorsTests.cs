using System.Net;
using Refit;

namespace SnapdInvest.Cli.Tests.Unit;

public sealed class CliErrorsTests
{
    [Fact]
    public void Format_ApiExceptionWithBody_PrefersBodyOverGenericMessage()
    {
        var apiException = BuildApiException(HttpStatusCode.InternalServerError,
            """{"detail":"upstream broker timed out"}""");

        var formatted = CliErrors.Format(apiException);

        formatted.ShouldContain("500");
        formatted.ShouldContain("upstream broker timed out");
    }

    [Fact]
    public void Format_NonApiException_FallsBackToMessage()
    {
        var ex = new InvalidOperationException("plain old failure");
        CliErrors.Format(ex).ShouldBe("plain old failure");
    }

    [Fact]
    public void Format_ApiExceptionWithoutBody_FallsBackToMessage()
    {
        var apiException = BuildApiException(HttpStatusCode.NotFound, body: null);
        var formatted = CliErrors.Format(apiException);
        formatted.ShouldNotContain("HTTP 404"); // empty Content → fallback path
    }

    private static ApiException BuildApiException(HttpStatusCode status, string? body)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, "http://test/v1/x");
        var response = new HttpResponseMessage(status)
        {
            Content = body is null ? null! : new StringContent(body),
            RequestMessage = request,
        };
        return ApiException.Create(request, HttpMethod.Get, response, new RefitSettings())
            .GetAwaiter().GetResult();
    }
}
