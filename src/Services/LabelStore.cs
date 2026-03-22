using System.Text.Json;
using System.Text.RegularExpressions;
using ClaudeSessionsViewer.Models;

namespace ClaudeSessionsViewer.Services;

public sealed partial class LabelStore
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    private static readonly IReadOnlyDictionary<string, string> LabelColorPresets =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["red"] = "#ef4444",
            ["blue"] = "#3b82f6",
            ["green"] = "#22c55e",
            ["yellow"] = "#eab308",
            ["purple"] = "#a855f7",
        };

    private static readonly IReadOnlyDictionary<string, string> LabelColorFamilyLabels =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["red"] = "赤系",
            ["blue"] = "青系",
            ["green"] = "緑系",
            ["yellow"] = "黄色系",
            ["purple"] = "紫系",
        };

    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly string _storagePath;
    private LabelStoreSnapshot? _cachedSnapshot;
    private DateTime _cachedLastWriteTimeUtc;

    public LabelStore(IWebHostEnvironment environment)
    {
        var cacheDir = Path.Combine(ResolveCacheRoot(environment.ContentRootPath), ".cache");
        Directory.CreateDirectory(cacheDir);
        _storagePath = Path.Combine(cacheDir, "label-store-claude.json");
    }

    private static string ResolveCacheRoot(string contentRootPath)
    {
        var configuredAppRoot = Environment.GetEnvironmentVariable("SESSIONS_VIEWER_APP_ROOT");
        if (!string.IsNullOrWhiteSpace(configuredAppRoot))
        {
            return Path.GetFullPath(configuredAppRoot);
        }

        var normalizedContentRoot = Path.GetFullPath(contentRootPath)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (string.Equals(Path.GetFileName(normalizedContentRoot), "payload", StringComparison.OrdinalIgnoreCase))
        {
            var payloadParent = Directory.GetParent(normalizedContentRoot)?.FullName;
            if (!string.IsNullOrWhiteSpace(payloadParent))
            {
                return payloadParent;
            }
        }

        var currentDirectory = Path.GetFullPath(Directory.GetCurrentDirectory())
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var currentDirectoryPayload = Path.Combine(currentDirectory, "payload")
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (string.Equals(currentDirectoryPayload, normalizedContentRoot, StringComparison.OrdinalIgnoreCase))
        {
            return currentDirectory;
        }

        var contentRootParent = Directory.GetParent(normalizedContentRoot)?.FullName;
        if (!string.IsNullOrWhiteSpace(contentRootParent))
        {
            var siblingAppDir = Path.Combine(contentRootParent, "app");
            if (Directory.Exists(siblingAppDir))
            {
                return siblingAppDir;
            }
        }

        return normalizedContentRoot;
    }

    public async Task<LabelStoreSnapshot> GetSnapshotAsync(CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var lastWrite = File.Exists(_storagePath)
                ? new FileInfo(_storagePath).LastWriteTimeUtc
                : DateTime.MinValue;

            if (_cachedSnapshot is not null && lastWrite == _cachedLastWriteTimeUtc)
            {
                return _cachedSnapshot;
            }

            var store = await ReadStoreAsync(cancellationToken);
            var snapshot = ToSnapshot(store);
            _cachedSnapshot = snapshot;
            _cachedLastWriteTimeUtc = lastWrite;
            return snapshot;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<LabelDto> SaveLabelAsync(int? labelId, string? name, string? colorValue, string? colorFamily, CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var store = await ReadStoreAsync(cancellationToken);
            var cleanName = (name ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(cleanName))
            {
                throw new InvalidOperationException("ラベル名を入力してください");
            }

            if (cleanName.Length > 60)
            {
                throw new InvalidOperationException("ラベル名が長すぎます");
            }

            var duplicate = store.Labels.FirstOrDefault(label =>
                string.Equals(label.Name, cleanName, StringComparison.OrdinalIgnoreCase) &&
                (!labelId.HasValue || label.Id != labelId.Value));
            if (duplicate is not null)
            {
                throw new InvalidOperationException("同名のラベルは既に存在します");
            }

            var (normalizedColor, normalizedFamily) = NormalizeLabelColor(colorValue, colorFamily);
            StoredLabel target;
            if (labelId is null)
            {
                target = new StoredLabel
                {
                    Id = store.NextLabelId++,
                    Name = cleanName,
                    ColorValue = normalizedColor,
                    ColorFamily = normalizedFamily,
                };
                store.Labels.Add(target);
            }
            else
            {
                target = store.Labels.FirstOrDefault(label => label.Id == labelId.Value)
                    ?? throw new InvalidOperationException("ラベルが見つかりません");
                target.Name = cleanName;
                target.ColorValue = normalizedColor;
                target.ColorFamily = normalizedFamily;
            }

            SortLabels(store.Labels);
            await WriteStoreAsync(store, cancellationToken);
            _cachedSnapshot = null;
            return ToDto(target);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task DeleteLabelAsync(int labelId, CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var store = await ReadStoreAsync(cancellationToken);
            store.Labels.RemoveAll(label => label.Id == labelId);

            foreach (var pair in store.SessionLabels.ToArray())
            {
                pair.Value.RemoveAll(value => value == labelId);
                if (pair.Value.Count == 0)
                {
                    store.SessionLabels.Remove(pair.Key);
                }
            }

            foreach (var sessionPair in store.EventLabels.ToArray())
            {
                foreach (var eventPair in sessionPair.Value.ToArray())
                {
                    eventPair.Value.RemoveAll(value => value == labelId);
                    if (eventPair.Value.Count == 0)
                    {
                        sessionPair.Value.Remove(eventPair.Key);
                    }
                }

                if (sessionPair.Value.Count == 0)
                {
                    store.EventLabels.Remove(sessionPair.Key);
                }
            }

            await WriteStoreAsync(store, cancellationToken);
            _cachedSnapshot = null;
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task AddSessionLabelAsync(string sessionPath, int labelId, CancellationToken cancellationToken = default)
    {
        await MutateAsync(store =>
        {
            EnsureLabelExists(store, labelId);
            if (!store.SessionLabels.TryGetValue(sessionPath, out var labelIds))
            {
                labelIds = [];
                store.SessionLabels[sessionPath] = labelIds;
            }

            if (!labelIds.Contains(labelId))
            {
                labelIds.Add(labelId);
            }
        }, cancellationToken);
    }

    public async Task RemoveSessionLabelAsync(string sessionPath, int labelId, CancellationToken cancellationToken = default)
    {
        await MutateAsync(store =>
        {
            if (!store.SessionLabels.TryGetValue(sessionPath, out var labelIds))
            {
                return;
            }

            labelIds.RemoveAll(value => value == labelId);
            if (labelIds.Count == 0)
            {
                store.SessionLabels.Remove(sessionPath);
            }
        }, cancellationToken);
    }

    public async Task AddEventLabelAsync(string sessionPath, string eventId, int labelId, CancellationToken cancellationToken = default)
    {
        await MutateAsync(store =>
        {
            EnsureLabelExists(store, labelId);
            if (!store.EventLabels.TryGetValue(sessionPath, out var eventMap))
            {
                eventMap = new Dictionary<string, List<int>>(StringComparer.Ordinal);
                store.EventLabels[sessionPath] = eventMap;
            }

            if (!eventMap.TryGetValue(eventId, out var labelIds))
            {
                labelIds = [];
                eventMap[eventId] = labelIds;
            }

            if (!labelIds.Contains(labelId))
            {
                labelIds.Add(labelId);
            }
        }, cancellationToken);
    }

    public async Task RemoveEventLabelAsync(string sessionPath, string eventId, int labelId, CancellationToken cancellationToken = default)
    {
        await MutateAsync(store =>
        {
            if (!store.EventLabels.TryGetValue(sessionPath, out var eventMap))
            {
                return;
            }

            if (!eventMap.TryGetValue(eventId, out var labelIds))
            {
                return;
            }

            labelIds.RemoveAll(value => value == labelId);
            if (labelIds.Count == 0)
            {
                eventMap.Remove(eventId);
            }

            if (eventMap.Count == 0)
            {
                store.EventLabels.Remove(sessionPath);
            }
        }, cancellationToken);
    }

    private async Task MutateAsync(Action<StoredLabelStore> action, CancellationToken cancellationToken)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            var store = await ReadStoreAsync(cancellationToken);
            action(store);
            await WriteStoreAsync(store, cancellationToken);
            _cachedSnapshot = null;
        }
        finally
        {
            _gate.Release();
        }
    }

    private async Task<StoredLabelStore> ReadStoreAsync(CancellationToken cancellationToken)
    {
        if (!File.Exists(_storagePath))
        {
            return new StoredLabelStore();
        }

        await using var stream = File.OpenRead(_storagePath);
        var store = await JsonSerializer.DeserializeAsync<StoredLabelStore>(stream, SerializerOptions, cancellationToken);
        if (store is null)
        {
            return new StoredLabelStore();
        }

        store.Labels ??= [];
        store.SessionLabels ??= new Dictionary<string, List<int>>(StringComparer.Ordinal);
        store.EventLabels ??= new Dictionary<string, Dictionary<string, List<int>>>(StringComparer.Ordinal);
        if (store.NextLabelId <= 0)
        {
            store.NextLabelId = Math.Max(1, store.Labels.DefaultIfEmpty().Max(label => label?.Id ?? 0) + 1);
        }

        SortLabels(store.Labels);
        return store;
    }

    private async Task WriteStoreAsync(StoredLabelStore store, CancellationToken cancellationToken)
    {
        store.NextLabelId = Math.Max(store.NextLabelId, store.Labels.DefaultIfEmpty().Max(label => label?.Id ?? 0) + 1);
        var tempPath = $"{_storagePath}.tmp";
        await using (var stream = File.Create(tempPath))
        {
            await JsonSerializer.SerializeAsync(stream, store, SerializerOptions, cancellationToken);
        }

        File.Move(tempPath, _storagePath, overwrite: true);
    }

    private static LabelStoreSnapshot ToSnapshot(StoredLabelStore store)
    {
        var labels = store.Labels.Select(ToDto).ToList();
        var labelById = labels.ToDictionary(label => label.Id);
        var sessionLabels = new Dictionary<string, IReadOnlyList<int>>(StringComparer.Ordinal);
        foreach (var pair in store.SessionLabels)
        {
            sessionLabels[pair.Key] = pair.Value.Distinct().OrderBy(value => value).ToArray();
        }

        var eventLabels = new Dictionary<string, IReadOnlyDictionary<string, IReadOnlyList<int>>>(StringComparer.Ordinal);
        foreach (var sessionPair in store.EventLabels)
        {
            var eventMap = new Dictionary<string, IReadOnlyList<int>>(StringComparer.Ordinal);
            foreach (var eventPair in sessionPair.Value)
            {
                eventMap[eventPair.Key] = eventPair.Value.Distinct().OrderBy(value => value).ToArray();
            }

            eventLabels[sessionPair.Key] = eventMap;
        }

        return new LabelStoreSnapshot(labels, labelById, sessionLabels, eventLabels);
    }

    private static void EnsureLabelExists(StoredLabelStore store, int labelId)
    {
        if (store.Labels.All(label => label.Id != labelId))
        {
            throw new InvalidOperationException("ラベルが見つかりません");
        }
    }

    private static LabelDto ToDto(StoredLabel label)
    {
        var family = (label.ColorFamily ?? string.Empty).Trim();
        return new LabelDto
        {
            Id = label.Id,
            Name = label.Name ?? string.Empty,
            ColorValue = label.ColorValue ?? string.Empty,
            ColorFamily = family,
            ColorFamilyLabel = LabelColorFamilyLabels.TryGetValue(family, out var value) ? value : string.Empty,
        };
    }

    private static void SortLabels(List<StoredLabel> labels)
    {
        labels.Sort((left, right) =>
        {
            var byName = StringComparer.OrdinalIgnoreCase.Compare(left.Name, right.Name);
            return byName != 0 ? byName : left.Id.CompareTo(right.Id);
        });
    }

    private static (string ColorValue, string ColorFamily) NormalizeLabelColor(string? colorValue, string? colorFamily)
    {
        var family = (colorFamily ?? string.Empty).Trim().ToLowerInvariant();
        if (!LabelColorPresets.ContainsKey(family))
        {
            family = string.Empty;
        }

        var value = (colorValue ?? string.Empty).Trim();
        if (!string.IsNullOrEmpty(value))
        {
            if (!IsSafeCssColor(value))
            {
                throw new InvalidOperationException("色コードの形式が不正です");
            }

            return (value, family);
        }

        if (!string.IsNullOrEmpty(family))
        {
            return (LabelColorPresets[family], family);
        }

        throw new InvalidOperationException("色コードを入力してください");
    }

    private static bool IsSafeCssColor(string value)
    {
        var candidate = value.Trim();
        if (candidate.Length == 0 || candidate.Length > 64)
        {
            return false;
        }

        if (!AllowedCssColorCharsRegex().IsMatch(candidate))
        {
            return false;
        }

        if (HexColorRegex().IsMatch(candidate))
        {
            return true;
        }

        var lowered = candidate.ToLowerInvariant();
        return RgbColorRegex().IsMatch(lowered) || OklchColorRegex().IsMatch(lowered);
    }

    [GeneratedRegex(@"^[#(),.%/\-\sa-zA-Z0-9]+$")]
    private static partial Regex AllowedCssColorCharsRegex();

    [GeneratedRegex(@"^#[0-9a-fA-F]{3,8}$")]
    private static partial Regex HexColorRegex();

    [GeneratedRegex(@"^rgba?\([^()]+\)$")]
    private static partial Regex RgbColorRegex();

    [GeneratedRegex(@"^oklch\([^()]+\)$")]
    private static partial Regex OklchColorRegex();

    private sealed class StoredLabelStore
    {
        public int NextLabelId { get; set; } = 1;

        public List<StoredLabel> Labels { get; set; } = [];

        public Dictionary<string, List<int>> SessionLabels { get; set; } = new(StringComparer.Ordinal);

        public Dictionary<string, Dictionary<string, List<int>>> EventLabels { get; set; } = new(StringComparer.Ordinal);
    }

    private sealed class StoredLabel
    {
        public int Id { get; set; }

        public string Name { get; set; } = string.Empty;

        public string ColorValue { get; set; } = string.Empty;

        public string ColorFamily { get; set; } = string.Empty;
    }
}

public sealed record LabelStoreSnapshot(
    IReadOnlyList<LabelDto> Labels,
    IReadOnlyDictionary<int, LabelDto> LabelById,
    IReadOnlyDictionary<string, IReadOnlyList<int>> SessionLabels,
    IReadOnlyDictionary<string, IReadOnlyDictionary<string, IReadOnlyList<int>>> EventLabels);
