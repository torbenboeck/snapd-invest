using System.Reflection;
using SnapdInvest.Cli.Commands;
using SnapdInvest.Client.Models;

namespace SnapdInvest.Cli.Tests.Unit.Commands;

/// <summary>
/// Unit-level tests for the static modification-parsing logic in <see cref="ApproveCommand"/>.
/// End-to-end command tests live in the EndToEnd test project (added later) using an in-memory
/// engine.
/// </summary>
public sealed class ApproveCommandTests
{
    private static List<SignalModificationDto> ParseModifications(string[] inputs)
    {
        var method = typeof(ApproveCommand).GetMethod(
            "ParseModifications",
            BindingFlags.NonPublic | BindingFlags.Static)!;
        return (List<SignalModificationDto>)method.Invoke(null, [inputs])!;
    }

    [Fact]
    public void ParseModifications_QuantityModification_Parsed()
    {
        var result = ParseModifications(["AAPL@NASDAQ=2.5"]);

        result.Count.ShouldBe(1);
        result[0].InstrumentSymbol.ShouldBe("AAPL");
        result[0].InstrumentExchange.ShouldBe("NASDAQ");
        result[0].Quantity.ShouldBe(2.5m);
        result[0].Skip.ShouldBeFalse();
    }

    [Fact]
    public void ParseModifications_Skip_Parsed()
    {
        var result = ParseModifications(["TSLA@NASDAQ=skip"]);

        result.Count.ShouldBe(1);
        result[0].InstrumentSymbol.ShouldBe("TSLA");
        result[0].Quantity.ShouldBeNull();
        result[0].Skip.ShouldBeTrue();
    }

    [Fact]
    public void ParseModifications_MissingEquals_Throws()
    {
        Should.Throw<TargetInvocationException>(
                () => ParseModifications(["AAPL@NASDAQ"]))
            .InnerException.ShouldBeOfType<ArgumentException>();
    }

    [Fact]
    public void ParseModifications_MissingExchange_Throws()
    {
        Should.Throw<TargetInvocationException>(
                () => ParseModifications(["AAPL=5"]))
            .InnerException.ShouldBeOfType<ArgumentException>();
    }

    [Fact]
    public void ParseModifications_InvalidQuantity_Throws()
    {
        Should.Throw<TargetInvocationException>(
                () => ParseModifications(["AAPL@NASDAQ=notanumber"]))
            .InnerException.ShouldBeOfType<ArgumentException>();
    }
}
