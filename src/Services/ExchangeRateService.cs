using System.Globalization;
using System.Net.Http.Headers;
using System.Text.Json;
using ClaudeSessionsViewer.Models;

namespace ClaudeSessionsViewer.Services;

public sealed class ExchangeRateService
{
    private const string SectionName = "ExchangeRates";
    private const string DefaultEndpoint = "https://cdn.moneyconvert.net/api/latest.json";
    private static readonly TimeSpan DefaultRefreshInterval = TimeSpan.FromMinutes(10);

    private readonly IConfiguration _configuration;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly ILogger<ExchangeRateService> _logger;
    private readonly SemaphoreSlim _refreshLock = new(1, 1);

    private ExchangeRateSnapshot _snapshot = ExchangeRateSnapshot.Empty;

    public ExchangeRateService(
        IConfiguration configuration,
        IHttpClientFactory httpClientFactory,
        ILogger<ExchangeRateService> logger)
    {
        _configuration = configuration;
        _httpClientFactory = httpClientFactory;
        _logger = logger;
    }

    public async Task<ExchangeRateDto?> GetUsdJpyRateAsync(CancellationToken cancellationToken = default)
    {
        var settings = GetSettings();
        var now = DateTimeOffset.UtcNow;
        var snapshot = _snapshot;
        if (snapshot.ShouldRefresh(now, settings.RefreshInterval))
        {
            await RefreshAsync(settings, now, cancellationToken);
            snapshot = _snapshot;
        }

        return snapshot.ToDto();
    }

    private async Task RefreshAsync(ExchangeRateSettings settings, DateTimeOffset now, CancellationToken cancellationToken)
    {
        await _refreshLock.WaitAsync(cancellationToken);
        try
        {
            var current = _snapshot;
            if (!current.ShouldRefresh(now, settings.RefreshInterval))
            {
                return;
            }

            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, settings.Endpoint);
                request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
                request.Headers.UserAgent.ParseAdd("ClaudeSessionsViewer/1.0");

                var client = _httpClientFactory.CreateClient("exchange-rates");
                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
                response.EnsureSuccessStatusCode();

                await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
                using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
                var jpyRate = ReadRequiredRate(document.RootElement, "JPY");
                var cnyRate = ReadRequiredRate(document.RootElement, "CNY");
                var twdRate = ReadRequiredRate(document.RootElement, "TWD");
                var hkdRate = ReadRequiredRate(document.RootElement, "HKD");
                var baseCurrency = ReadOptionalString(document.RootElement, "base", "USD");

                _snapshot = current.WithSuccess(
                    settings.Endpoint,
                    baseCurrency,
                    jpyRate,
                    cnyRate,
                    twdRate,
                    hkdRate,
                    now);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                _snapshot = current.WithFailure(settings.Endpoint, now, ex.Message);
                _logger.LogWarning(ex, "Failed to refresh USD exchange rates from {Endpoint}", settings.Endpoint);
            }
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    private ExchangeRateSettings GetSettings()
    {
        var endpoint = _configuration.GetValue<string>($"{SectionName}:Endpoint");
        var refreshIntervalMinutes = _configuration.GetValue<int?>($"{SectionName}:RefreshIntervalMinutes");
        return new ExchangeRateSettings(
            string.IsNullOrWhiteSpace(endpoint) ? DefaultEndpoint : endpoint.Trim(),
            refreshIntervalMinutes.HasValue && refreshIntervalMinutes.Value > 0
                ? TimeSpan.FromMinutes(refreshIntervalMinutes.Value)
                : DefaultRefreshInterval);
    }

    private static decimal ReadRequiredRate(JsonElement root, string currency)
    {
        if (!root.TryGetProperty("rates", out var rates) || rates.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException("Exchange-rate response does not contain a rates object.");
        }

        if (!rates.TryGetProperty(currency, out var rateElement))
        {
            throw new InvalidOperationException($"Exchange-rate response does not contain a {currency} rate.");
        }

        if (rateElement.ValueKind == JsonValueKind.Number && rateElement.TryGetDecimal(out var rate))
        {
            return rate;
        }

        if (rateElement.ValueKind == JsonValueKind.String
            && decimal.TryParse(rateElement.GetString(), NumberStyles.Number, CultureInfo.InvariantCulture, out rate))
        {
            return rate;
        }

        throw new InvalidOperationException($"Exchange-rate response contains an invalid {currency} rate.");
    }

    private static string ReadOptionalString(JsonElement root, string propertyName, string fallback)
    {
        if (!root.TryGetProperty(propertyName, out var value) || value.ValueKind != JsonValueKind.String)
        {
            return fallback;
        }

        var text = value.GetString();
        return string.IsNullOrWhiteSpace(text) ? fallback : text.Trim().ToUpperInvariant();
    }

    private sealed record ExchangeRateSettings(string Endpoint, TimeSpan RefreshInterval);

    private sealed record ExchangeRateSnapshot(
        string Endpoint,
        string BaseCurrency,
        decimal? JpyRate,
        decimal? CnyRate,
        decimal? TwdRate,
        decimal? HkdRate,
        DateTimeOffset FetchedAt,
        DateTimeOffset LastAttemptedAt,
        string LastError)
    {
        public static ExchangeRateSnapshot Empty { get; } = new(
            string.Empty,
            "USD",
            null,
            null,
            null,
            null,
            default,
            default,
            string.Empty);

        public bool ShouldRefresh(DateTimeOffset now, TimeSpan refreshInterval)
        {
            return LastAttemptedAt == default || now - LastAttemptedAt >= refreshInterval;
        }

        public ExchangeRateDto? ToDto()
        {
            if (!JpyRate.HasValue && !CnyRate.HasValue && !TwdRate.HasValue && !HkdRate.HasValue)
            {
                return null;
            }

            return new ExchangeRateDto
            {
                BaseCurrency = BaseCurrency,
                JpyRate = JpyRate ?? 0m,
                CnyRate = CnyRate ?? 0m,
                TwdRate = TwdRate ?? 0m,
                HkdRate = HkdRate ?? 0m,
                FetchedAt = FetchedAt.ToString("O", CultureInfo.InvariantCulture),
            };
        }

        public ExchangeRateSnapshot WithSuccess(
            string endpoint,
            string baseCurrency,
            decimal jpyRate,
            decimal cnyRate,
            decimal twdRate,
            decimal hkdRate,
            DateTimeOffset fetchedAt)
        {
            return this with
            {
                Endpoint = endpoint,
                BaseCurrency = baseCurrency,
                JpyRate = jpyRate,
                CnyRate = cnyRate,
                TwdRate = twdRate,
                HkdRate = hkdRate,
                FetchedAt = fetchedAt,
                LastAttemptedAt = fetchedAt,
                LastError = string.Empty,
            };
        }

        public ExchangeRateSnapshot WithFailure(string endpoint, DateTimeOffset attemptedAt, string error)
        {
            return this with
            {
                Endpoint = endpoint,
                LastAttemptedAt = attemptedAt,
                LastError = error,
            };
        }
    }
}
