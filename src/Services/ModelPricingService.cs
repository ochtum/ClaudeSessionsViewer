using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ClaudeSessionsViewer.Services;

public sealed class ModelPricingService
{
    private const string PricingSectionName = "Pricing";
    private const string DefaultCatalogPath = "model-pricing.json";
    private static readonly StringComparison PathComparison = OperatingSystem.IsWindows()
        ? StringComparison.OrdinalIgnoreCase
        : StringComparison.Ordinal;
    private static readonly JsonSerializerOptions PricingJsonOptions = new()
    {
        AllowTrailingCommas = true,
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };
    private static readonly JsonDocumentOptions PricingJsonDocumentOptions = new()
    {
        AllowTrailingCommas = true,
        CommentHandling = JsonCommentHandling.Skip,
    };

    private readonly IHostEnvironment _hostEnvironment;
    private readonly IConfiguration _configuration;
    private readonly ILogger<ModelPricingService> _logger;
    private readonly object _sync = new();

    private PricingCatalogSnapshot? _snapshot;

    public ModelPricingService(
        IHostEnvironment hostEnvironment,
        IConfiguration configuration,
        ILogger<ModelPricingService> logger)
    {
        _hostEnvironment = hostEnvironment;
        _configuration = configuration;
        _logger = logger;
    }

    public long GetCatalogVersion()
    {
        return GetPricingCatalog().Version;
    }

    public decimal? TryCalculateCostUsd(
        string rawModel,
        long inputTokens,
        long outputTokens,
        long cacheCreationTokens,
        long cacheReadTokens)
    {
        return TryCalculateCostBreakdownUsd(
            rawModel,
            inputTokens,
            outputTokens,
            cacheCreationTokens,
            cacheReadTokens)?.TotalCostUsd;
    }

    public CostBreakdownUsd? TryCalculateCostBreakdownUsd(
        string rawModel,
        long inputTokens,
        long outputTokens,
        long cacheCreationTokens,
        long cacheReadTokens)
    {
        if (!TryResolvePricing(rawModel, out var pricing))
        {
            return null;
        }

        var inputCost = CalculateTieredTokenCost(
            inputTokens,
            pricing.InputCostPerMillionTokens,
            pricing.InputCostPerMillionTokensAbove200k);
        var outputCost = CalculateTieredTokenCost(
            outputTokens,
            pricing.OutputCostPerMillionTokens,
            pricing.OutputCostPerMillionTokensAbove200k);
        var cacheCreationCost = CalculateTieredTokenCost(
            cacheCreationTokens,
            pricing.CacheCreationInputCostPerMillionTokens,
            pricing.CacheCreationInputCostPerMillionTokensAbove200k);
        var cacheReadCost = CalculateTieredTokenCost(
            cacheReadTokens,
            pricing.CachedInputCostPerMillionTokens,
            pricing.CachedInputCostPerMillionTokensAbove200k);
        return new CostBreakdownUsd(
            inputCost,
            cacheCreationCost,
            cacheReadCost,
            outputCost,
            inputCost + cacheCreationCost + cacheReadCost + outputCost);
    }

    private bool TryResolvePricing(string rawModel, out PricingCatalogEntry pricing)
    {
        var trimmed = rawModel.Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            pricing = default!;
            return false;
        }

        var catalog = GetPricingCatalog();
        foreach (var candidate in BuildPricingCandidates(trimmed))
        {
            if (TryResolvePricingCandidate(catalog, candidate, out pricing))
            {
                return true;
            }
        }

        pricing = default!;
        return false;
    }

    private static IReadOnlyList<string> BuildPricingCandidates(string rawModel)
    {
        var candidates = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        AddCandidate(rawModel);
        AddCandidate(NormalizePricingModel(rawModel));

        return candidates;

        void AddCandidate(string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return;
            }

            var trimmed = value.Trim();
            if (seen.Add(trimmed))
            {
                candidates.Add(trimmed);
            }
        }
    }

    private static bool TryResolvePricingCandidate(
        PricingCatalogSnapshot catalog,
        string candidate,
        out PricingCatalogEntry pricing)
    {
        var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var current = candidate;

        while (!string.IsNullOrWhiteSpace(current) && visited.Add(current))
        {
            if (TryGetExactOrVersionedModelPricing(catalog, current, out pricing))
            {
                return true;
            }

            if (!TryMapAlias(catalog, current, out current))
            {
                break;
            }
        }

        pricing = default!;
        return false;
    }

    private static bool TryGetExactOrVersionedModelPricing(
        PricingCatalogSnapshot catalog,
        string model,
        out PricingCatalogEntry pricing)
    {
        if (catalog.Models.TryGetValue(model, out pricing!))
        {
            return true;
        }

        foreach (var pair in catalog.Models.OrderByDescending(item => item.Key.Length))
        {
            if (model.StartsWith(pair.Key + "-", StringComparison.OrdinalIgnoreCase))
            {
                pricing = pair.Value;
                return true;
            }
        }

        pricing = default!;
        return false;
    }

    private static bool TryMapAlias(PricingCatalogSnapshot catalog, string model, out string alias)
    {
        if (catalog.Aliases.TryGetValue(model, out alias!))
        {
            return true;
        }

        foreach (var pair in catalog.Aliases.OrderByDescending(item => item.Key.Length))
        {
            if (model.StartsWith(pair.Key + "-", StringComparison.OrdinalIgnoreCase))
            {
                alias = pair.Value;
                return true;
            }
        }

        alias = string.Empty;
        return false;
    }

    private PricingCatalogSnapshot GetPricingCatalog()
    {
        var resolvedPath = ResolvePricingCatalogPath();
        var fileInfo = new FileInfo(resolvedPath);
        var version = ComputeCatalogVersion(resolvedPath, fileInfo);

        var cached = _snapshot;
        if (cached is not null
            && cached.Version == version
            && string.Equals(cached.Path, resolvedPath, PathComparison))
        {
            return cached;
        }

        lock (_sync)
        {
            cached = _snapshot;
            if (cached is not null
                && cached.Version == version
                && string.Equals(cached.Path, resolvedPath, PathComparison))
            {
                return cached;
            }

            _snapshot = LoadPricingCatalog(resolvedPath, fileInfo, version);
            return _snapshot;
        }
    }

    private PricingCatalogSnapshot LoadPricingCatalog(string path, FileInfo fileInfo, long version)
    {
        if (!fileInfo.Exists)
        {
            _logger.LogWarning("Pricing catalog file was not found: {Path}", path);
            return new PricingCatalogSnapshot(
                path,
                version,
                null,
                new Dictionary<string, PricingCatalogEntry>(StringComparer.OrdinalIgnoreCase),
                new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase));
        }

        using var stream = fileInfo.OpenRead();
        using var jsonDocument = JsonDocument.Parse(stream, PricingJsonDocumentOptions);
        var document = ConvertPricingCatalogDocument(jsonDocument.RootElement);

        return CreatePricingCatalogSnapshot(path, version, fileInfo.LastWriteTimeUtc, document);
    }

    private static PricingCatalogDocument ConvertPricingCatalogDocument(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
        {
            return new PricingCatalogDocument();
        }

        if (root.TryGetProperty("models", out _))
        {
            return root.Deserialize<PricingCatalogDocument>(PricingJsonOptions)
                ?? new PricingCatalogDocument();
        }

        return ConvertLiteLlmCatalog(root);
    }

    private static PricingCatalogSnapshot CreatePricingCatalogSnapshot(
        string path,
        long version,
        DateTimeOffset? lastWriteTimeUtc,
        PricingCatalogDocument document)
    {
        var models = new Dictionary<string, PricingCatalogEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (var pair in document.Models ?? new Dictionary<string, PricingCatalogEntry>())
        {
            if (!string.IsNullOrWhiteSpace(pair.Key))
            {
                models[pair.Key.Trim()] = pair.Value;
            }
        }

        var aliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pair in document.Aliases ?? new Dictionary<string, string>())
        {
            if (!string.IsNullOrWhiteSpace(pair.Key) && !string.IsNullOrWhiteSpace(pair.Value))
            {
                aliases[pair.Key.Trim()] = pair.Value.Trim();
            }
        }

        return new PricingCatalogSnapshot(path, version, lastWriteTimeUtc, models, aliases);
    }

    private static PricingCatalogDocument ConvertLiteLlmCatalog(JsonElement root)
    {
        var models = new Dictionary<string, PricingCatalogEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (var property in root.EnumerateObject())
        {
            if (property.NameEquals("sample_spec") || property.Value.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            if (!TryConvertLiteLlmPricingEntry(property.Value, out var entry))
            {
                continue;
            }

            models[property.Name] = entry;
        }

        return new PricingCatalogDocument
        {
            Models = models,
            Aliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase),
        };
    }

    private string ResolvePricingCatalogPath()
    {
        var configuredPath = _configuration.GetValue<string>($"{PricingSectionName}:CatalogPath");
        var relativePath = string.IsNullOrWhiteSpace(configuredPath)
            ? DefaultCatalogPath
            : configuredPath.Trim();
        return ResolveAppRelativePath(relativePath);
    }

    private string ResolveAppRelativePath(string path)
    {
        if (Path.IsPathRooted(path))
        {
            return Path.GetFullPath(path);
        }

        return Path.GetFullPath(Path.Combine(_hostEnvironment.ContentRootPath, path));
    }

    private static string NormalizePricingModel(string rawModel)
    {
        var model = rawModel.Trim();
        foreach (var prefix in new[]
                 {
                     "anthropic/",
                     "bedrock/anthropic/",
                     "vertex_ai/anthropic/",
                     "openrouter/anthropic/",
                     "openrouter/",
                 })
        {
            if (model.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                return model[prefix.Length..];
            }
        }

        return model;
    }

    private static bool TryConvertLiteLlmPricingEntry(JsonElement element, out PricingCatalogEntry entry)
    {
        var hasInputCost = TryReadDecimalProperty(element, "input_cost_per_token", out var inputCostPerToken);
        var hasInputCostAbove200k = TryReadDecimalProperty(
            element,
            ["input_cost_per_token_above_200k_tokens", "input_cost_per_token_above_200k"],
            out var inputCostPerTokenAbove200k);
        var hasCacheCreationCost = TryReadDecimalProperty(
            element,
            ["cache_creation_input_token_cost", "cache_creation_input_cost_per_token"],
            out var cacheCreationCostPerToken);
        var hasCacheCreationCostAbove200k = TryReadDecimalProperty(
            element,
            [
                "cache_creation_input_token_cost_above_200k_tokens",
                "cache_creation_input_cost_per_token_above_200k",
            ],
            out var cacheCreationCostPerTokenAbove200k);
        var hasCachedInputCost = TryReadDecimalProperty(
            element,
            ["cache_read_input_token_cost", "cached_input_cost_per_token"],
            out var cachedInputCostPerToken);
        var hasCachedInputCostAbove200k = TryReadDecimalProperty(
            element,
            [
                "cache_read_input_token_cost_above_200k_tokens",
                "cached_input_cost_per_token_above_200k",
            ],
            out var cachedInputCostPerTokenAbove200k);
        var hasOutputCost = TryReadDecimalProperty(element, "output_cost_per_token", out var outputCostPerToken);
        var hasOutputCostAbove200k = TryReadDecimalProperty(
            element,
            ["output_cost_per_token_above_200k_tokens", "output_cost_per_token_above_200k"],
            out var outputCostPerTokenAbove200k);

        if (!hasInputCost && !hasCacheCreationCost && !hasCachedInputCost && !hasOutputCost)
        {
            entry = default!;
            return false;
        }

        entry = new PricingCatalogEntry
        {
            InputCostPerMillionTokens = hasInputCost ? inputCostPerToken * 1_000_000m : 0m,
            InputCostPerMillionTokensAbove200k = hasInputCostAbove200k ? inputCostPerTokenAbove200k * 1_000_000m : null,
            CacheCreationInputCostPerMillionTokens = hasCacheCreationCost
                ? cacheCreationCostPerToken * 1_000_000m
                : hasInputCost
                    ? inputCostPerToken * 1_000_000m
                    : 0m,
            CacheCreationInputCostPerMillionTokensAbove200k = hasCacheCreationCostAbove200k
                ? cacheCreationCostPerTokenAbove200k * 1_000_000m
                : hasInputCostAbove200k
                    ? inputCostPerTokenAbove200k * 1_000_000m
                    : null,
            CachedInputCostPerMillionTokens = hasCachedInputCost ? cachedInputCostPerToken * 1_000_000m : 0m,
            CachedInputCostPerMillionTokensAbove200k = hasCachedInputCostAbove200k ? cachedInputCostPerTokenAbove200k * 1_000_000m : null,
            OutputCostPerMillionTokens = hasOutputCost ? outputCostPerToken * 1_000_000m : 0m,
            OutputCostPerMillionTokensAbove200k = hasOutputCostAbove200k ? outputCostPerTokenAbove200k * 1_000_000m : null,
        };
        return true;
    }

    private static bool TryReadDecimalProperty(JsonElement element, string propertyName, out decimal value)
    {
        value = 0m;
        if (!element.TryGetProperty(propertyName, out var property))
        {
            return false;
        }

        if (property.ValueKind == JsonValueKind.Number && property.TryGetDecimal(out value))
        {
            return true;
        }

        return property.ValueKind == JsonValueKind.String
            && decimal.TryParse(
                property.GetString(),
                NumberStyles.Float | NumberStyles.AllowThousands,
                CultureInfo.InvariantCulture,
                out value);
    }

    private static bool TryReadDecimalProperty(JsonElement element, IEnumerable<string> propertyNames, out decimal value)
    {
        foreach (var propertyName in propertyNames)
        {
            if (TryReadDecimalProperty(element, propertyName, out value))
            {
                return true;
            }
        }

        value = 0m;
        return false;
    }

    private static decimal CalculateTieredTokenCost(
        long tokens,
        decimal unitCostPerMillionTokens,
        decimal? unitCostPerMillionTokensAbove200k)
    {
        if (tokens <= 0)
        {
            return 0m;
        }

        var unitCost = unitCostPerMillionTokens / 1_000_000m;
        if (!unitCostPerMillionTokensAbove200k.HasValue || tokens <= 200_000)
        {
            return tokens * unitCost;
        }

        var unitCostAbove200k = unitCostPerMillionTokensAbove200k.Value / 1_000_000m;
        var baseTokens = 200_000;
        var aboveTokens = tokens - baseTokens;
        return (baseTokens * unitCost) + (aboveTokens * unitCostAbove200k);
    }

    private static long ComputeCatalogVersion(string path, FileInfo fileInfo)
    {
        return HashCode.Combine(
            path,
            fileInfo.Exists ? fileInfo.LastWriteTimeUtc.Ticks : 0L,
            fileInfo.Exists ? fileInfo.Length : 0L);
    }

    public readonly record struct CostBreakdownUsd(
        decimal InputCostUsd,
        decimal CacheCreationCostUsd,
        decimal CacheReadCostUsd,
        decimal OutputCostUsd,
        decimal TotalCostUsd);

    private sealed record PricingCatalogSnapshot(
        string Path,
        long Version,
        DateTimeOffset? LastWriteTimeUtc,
        IReadOnlyDictionary<string, PricingCatalogEntry> Models,
        IReadOnlyDictionary<string, string> Aliases);

    private sealed record PricingCatalogDocument
    {
        [JsonPropertyName("models")]
        public Dictionary<string, PricingCatalogEntry>? Models { get; init; }

        [JsonPropertyName("aliases")]
        public Dictionary<string, string>? Aliases { get; init; }
    }

    private sealed record PricingCatalogEntry
    {
        [JsonPropertyName("input_cost_per_million_tokens")]
        public decimal InputCostPerMillionTokens { get; init; }

        [JsonPropertyName("input_cost_per_million_tokens_above_200k")]
        public decimal? InputCostPerMillionTokensAbove200k { get; init; }

        [JsonPropertyName("cache_creation_input_cost_per_million_tokens")]
        public decimal CacheCreationInputCostPerMillionTokens { get; init; }

        [JsonPropertyName("cache_creation_input_cost_per_million_tokens_above_200k")]
        public decimal? CacheCreationInputCostPerMillionTokensAbove200k { get; init; }

        [JsonPropertyName("cached_input_cost_per_million_tokens")]
        public decimal CachedInputCostPerMillionTokens { get; init; }

        [JsonPropertyName("cached_input_cost_per_million_tokens_above_200k")]
        public decimal? CachedInputCostPerMillionTokensAbove200k { get; init; }

        [JsonPropertyName("output_cost_per_million_tokens")]
        public decimal OutputCostPerMillionTokens { get; init; }

        [JsonPropertyName("output_cost_per_million_tokens_above_200k")]
        public decimal? OutputCostPerMillionTokensAbove200k { get; init; }
    }
}
