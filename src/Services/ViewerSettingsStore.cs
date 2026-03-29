using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Nodes;

namespace ClaudeSessionsViewer.Services;

public sealed class ViewerSettingsStore
{
    private const int DefaultSessionListMax = 1000;
    private const int DefaultSessionListInitialLoadCount = 50;
    private const int DefaultSessionEventsMax = 10000;
    private const int MinLimit = 1;
    private const int MaxLimit = 100_000;
    private const string Crlf = "\r\n";

    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly object _gate = new();
    private readonly string _storagePath;
    private ViewerSettingsSnapshot? _cachedSnapshot;
    private DateTime _cachedLastWriteTimeUtc;

    public ViewerSettingsStore(IWebHostEnvironment environment)
    {
        var cacheDir = Path.Combine(AppStoragePathHelper.ResolveCacheRoot(environment.ContentRootPath), ".cache");
        Directory.CreateDirectory(cacheDir);
        _storagePath = Path.Combine(cacheDir, "viewer-settings.json");
        EnsureSettingsFileExists();
    }

    public ViewerSettingsSnapshot GetSnapshot()
    {
        lock (_gate)
        {
            EnsureSettingsFileExists();
            var lastWrite = File.Exists(_storagePath)
                ? new FileInfo(_storagePath).LastWriteTimeUtc
                : DateTime.MinValue;

            if (_cachedSnapshot is not null && lastWrite == _cachedLastWriteTimeUtc)
            {
                return _cachedSnapshot;
            }

            var dto = ReadSettings();
            var snapshot = new ViewerSettingsSnapshot(
                NormalizeLimit(dto.SessionListMax, DefaultSessionListMax),
                NormalizeLimit(dto.SessionListInitialLoadCount, DefaultSessionListInitialLoadCount),
                NormalizeLimit(dto.SessionEventsMax, DefaultSessionEventsMax),
                lastWrite.Ticks);
            _cachedSnapshot = snapshot;
            _cachedLastWriteTimeUtc = lastWrite;
            return snapshot;
        }
    }

    private void EnsureSettingsFileExists()
    {
        if (!File.Exists(_storagePath))
        {
            WriteSettingsFile(new ViewerSettingsFile
            {
                SessionListMax = DefaultSessionListMax,
                SessionListInitialLoadCount = DefaultSessionListInitialLoadCount,
                SessionEventsMax = DefaultSessionEventsMax,
            });
            return;
        }

        TryAddMissingInitialLoadCount();
    }

    private void TryAddMissingInitialLoadCount()
    {
        try
        {
            var json = File.ReadAllText(_storagePath);
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            using var document = JsonDocument.Parse(json);
            if (document.RootElement.ValueKind is not JsonValueKind.Object ||
                document.RootElement.TryGetProperty("session_list_initial_load_count", out _))
            {
                return;
            }

            var root = JsonNode.Parse(json) as JsonObject;
            if (root is null)
            {
                return;
            }

            root["session_list_initial_load_count"] = DefaultSessionListInitialLoadCount;
            WriteSettingsJson(root.ToJsonString(SerializerOptions));
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
        catch (JsonException)
        {
        }
    }

    private ViewerSettingsFile ReadSettings()
    {
        try
        {
            var json = File.ReadAllText(_storagePath);
            if (string.IsNullOrWhiteSpace(json))
            {
                return new ViewerSettingsFile();
            }

            return JsonSerializer.Deserialize<ViewerSettingsFile>(json, SerializerOptions)
                ?? new ViewerSettingsFile();
        }
        catch (IOException)
        {
            return new ViewerSettingsFile();
        }
        catch (UnauthorizedAccessException)
        {
            return new ViewerSettingsFile();
        }
        catch (JsonException)
        {
            return new ViewerSettingsFile();
        }
    }

    private void WriteSettingsFile(ViewerSettingsFile settings)
    {
        WriteSettingsJson(JsonSerializer.Serialize(settings, SerializerOptions));
    }

    private void WriteSettingsJson(string json)
    {
        File.WriteAllText(_storagePath, EnsureTrailingCrlf(NormalizeToCrlf(json)));
    }

    private static string NormalizeToCrlf(string value)
    {
        return value
            .Replace("\r\n", "\n", StringComparison.Ordinal)
            .Replace("\r", "\n", StringComparison.Ordinal)
            .Replace("\n", Crlf, StringComparison.Ordinal);
    }

    private static string EnsureTrailingCrlf(string value)
    {
        return value.EndsWith(Crlf, StringComparison.Ordinal)
            ? value
            : value + Crlf;
    }

    private static int NormalizeLimit(int? rawValue, int fallback)
    {
        if (!rawValue.HasValue)
        {
            return fallback;
        }

        return Math.Clamp(rawValue.Value, MinLimit, MaxLimit);
    }

    public sealed record ViewerSettingsSnapshot(
        int SessionListMax,
        int SessionListInitialLoadCount,
        int SessionEventsMax,
        long Version);

    private sealed class ViewerSettingsFile
    {
        [JsonPropertyName("session_list_max")]
        public int? SessionListMax { get; set; }

        [JsonPropertyName("session_list_initial_load_count")]
        public int? SessionListInitialLoadCount { get; set; }

        [JsonPropertyName("session_events_max")]
        public int? SessionEventsMax { get; set; }
    }
}
