using System.Text.Json;
using System.Text.Json.Serialization;

namespace ClaudeSessionsViewer.Services;

public sealed class ViewerSettingsStore
{
    private const int DefaultSessionListMax = 1000;
    private const int DefaultSessionEventsMax = 10000;
    private const int MinLimit = 1;
    private const int MaxLimit = 100_000;

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
        EnsureDefaultFileExists();
    }

    public ViewerSettingsSnapshot GetSnapshot()
    {
        lock (_gate)
        {
            EnsureDefaultFileExists();
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
                NormalizeLimit(dto.SessionEventsMax, DefaultSessionEventsMax),
                lastWrite.Ticks);
            _cachedSnapshot = snapshot;
            _cachedLastWriteTimeUtc = lastWrite;
            return snapshot;
        }
    }

    private void EnsureDefaultFileExists()
    {
        if (File.Exists(_storagePath))
        {
            return;
        }

        var defaults = new ViewerSettingsFile
        {
            SessionListMax = DefaultSessionListMax,
            SessionEventsMax = DefaultSessionEventsMax,
        };
        var json = JsonSerializer.Serialize(defaults, SerializerOptions);
        File.WriteAllText(_storagePath, json + Environment.NewLine);
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
        int SessionEventsMax,
        long Version);

    private sealed class ViewerSettingsFile
    {
        [JsonPropertyName("session_list_max")]
        public int? SessionListMax { get; set; }

        [JsonPropertyName("session_events_max")]
        public int? SessionEventsMax { get; set; }
    }
}
