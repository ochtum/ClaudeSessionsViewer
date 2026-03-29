using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using ClaudeSessionsViewer.Models;

namespace ClaudeSessionsViewer.Services;

public sealed class ViewerService
{
    private const int MaxDesktopScanBytes = 2 * 1024 * 1024;
    private const int SearchTextLimit = 50_000;
    private const int MaxCacheEntries = 2000;
    private static readonly TimeSpan SessionItemsCacheTtl = TimeSpan.FromSeconds(8);
    private static readonly TimeSpan CostSummaryCacheTtl = TimeSpan.FromMinutes(5);

    private static readonly string[] TextKeys =
    [
        "text",
        "content",
        "message",
        "prompt",
        "output",
        "input",
        "value",
        "body",
    ];

    private static readonly HashSet<string> SkipRecursiveKeys = new(StringComparer.Ordinal)
    {
        "type",
        "id",
        "uuid",
        "role",
        "sender",
        "author",
        "version",
        "updatedAt",
        "createdAt",
        "timestamp",
        "time",
        "ts",
    };

    private static readonly StringComparer PathComparer = OperatingSystem.IsWindows()
        ? StringComparer.OrdinalIgnoreCase
        : StringComparer.Ordinal;

    private static readonly StringComparison PathComparison = OperatingSystem.IsWindows()
        ? StringComparison.OrdinalIgnoreCase
        : StringComparison.Ordinal;

    private static readonly Regex WindowsPathRegex = new(@"^([A-Za-z]):[\\/](.*)$", RegexOptions.Compiled);
    private static readonly Regex WslMountPathRegex = new(@"^/mnt/([A-Za-z])(?:/(.*))?$", RegexOptions.Compiled);
    private static readonly Regex WslUncRootRegex = new(@"^\\\\wsl(?:\.localhost)?\\([^\\]+)(?:\\|$)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
    private static readonly Regex WhitespaceRegex = new(@"\s+", RegexOptions.Compiled);
    private static readonly Regex ReadableSnippetRegex = new(@"[ -~\u3040-\u30FF\u4E00-\u9FFF]{24,300}", RegexOptions.Compiled);

    private static readonly JsonSerializerOptions PrettyJsonOptions = new()
    {
        WriteIndented = true,
    };

    private readonly LabelStore _labelStore;
    private readonly ViewerSettingsStore _viewerSettings;
    private readonly ModelPricingService _modelPricing;
    private readonly ExchangeRateService _exchangeRates;
    private readonly string _contentRootPath;
    private readonly ConcurrentDictionary<string, SessionCacheEntry> _cache = new(PathComparer);
    private readonly SemaphoreSlim _costSummaryCacheLock = new(1, 1);
    private readonly object _sessionItemsCacheLock = new();
    private IReadOnlyList<string>? _cliRoots;
    private IReadOnlyList<string>? _desktopRoots;
    private IReadOnlyList<string>? _wslCliRootsOnWindows;
    private CostSummaryCacheEntry? _costSummaryCache;
    private SessionItemsCacheEntry? _sessionItemsCache;

    public ViewerService(
        LabelStore labelStore,
        ViewerSettingsStore viewerSettings,
        IHostEnvironment hostEnvironment,
        ModelPricingService modelPricing,
        ExchangeRateService exchangeRates)
    {
        _labelStore = labelStore;
        _viewerSettings = viewerSettings;
        _modelPricing = modelPricing;
        _exchangeRates = exchangeRates;
        _contentRootPath = CanonicalizePath(hostEnvironment.ContentRootPath);
    }

    public async Task<LabelsResponse> GetLabelsAsync(CancellationToken cancellationToken = default)
    {
        var snapshot = await _labelStore.GetSnapshotAsync(cancellationToken);
        return new LabelsResponse { Labels = snapshot.Labels };
    }

    public async Task<LabeledItemsResponse> GetLabeledItemsAsync(CancellationToken cancellationToken = default)
    {
        var snapshot = await _labelStore.GetSnapshotAsync(cancellationToken);
        var labeledSessions = new List<SessionSummaryDto>();
        var labeledEvents = new List<LabeledEventListItemDto>();

        foreach (var item in EnumerateAllSessionItems())
        {
            cancellationToken.ThrowIfCancellationRequested();

            IndexRecord record;
            try
            {
                record = GetOrBuildIndexRecord(item);
            }
            catch (FileNotFoundException)
            {
                continue;
            }

            var sessionPath = record.Summary.Path;
            if (snapshot.SessionLabels.TryGetValue(sessionPath, out var sessionLabelIds))
            {
                var labels = ResolveLabels(sessionLabelIds, snapshot.LabelById);
                if (labels.Count > 0)
                {
                    labeledSessions.Add(WithSessionLabels(record.Summary, sessionLabelIds, labels));
                }
            }

            if (snapshot.EventLabels.TryGetValue(sessionPath, out var labelsByEventId) && labelsByEventId.Count > 0)
            {
                EventsData eventsData;
                try
                {
                    eventsData = GetOrBuildEvents(item);
                }
                catch (FileNotFoundException)
                {
                    continue;
                }

                labeledEvents.AddRange(BuildLabeledEventItems(record.Summary, eventsData.Events, labelsByEventId, snapshot.LabelById));
            }
        }

        return new LabeledItemsResponse
        {
            Sessions = labeledSessions
                .OrderByDescending(GetSessionSortKey, StringComparer.Ordinal)
                .ThenByDescending(session => session.Mtime, StringComparer.Ordinal)
                .ToArray(),
            Events = labeledEvents
                .OrderByDescending(item => !string.IsNullOrWhiteSpace(item.Timestamp) ? item.Timestamp : item.SessionStartedAt, StringComparer.Ordinal)
                .ThenByDescending(item => item.SessionMtime, StringComparer.Ordinal)
                .ToArray(),
        };
    }

    public async Task<CostSummaryResponse> GetCostSummaryAsync(bool forceRefresh = false, CancellationToken cancellationToken = default)
    {
        var pricingVersion = _modelPricing.GetCatalogVersion();
        if (!forceRefresh && TryGetCachedCostSummary(pricingVersion, out var cached))
        {
            return cached;
        }

        await _costSummaryCacheLock.WaitAsync(cancellationToken);
        try
        {
            if (!forceRefresh && TryGetCachedCostSummary(pricingVersion, out cached))
            {
                return cached;
            }

            var response = await BuildCostSummaryAsync(cancellationToken);
            _costSummaryCache = new CostSummaryCacheEntry(DateTimeOffset.UtcNow, pricingVersion, response);
            return response;
        }
        finally
        {
            _costSummaryCacheLock.Release();
        }
    }

    private async Task<CostSummaryResponse> BuildCostSummaryAsync(CancellationToken cancellationToken = default)
    {
        var nowLocal = DateTime.Now;
        var exchangeRate = await _exchangeRates.GetUsdJpyRateAsync(cancellationToken);
        var groups = BuildCostSummaryGroupDefinitions(nowLocal)
            .Select(definition => new CostSummaryGroupAccumulator(definition))
            .ToArray();

        foreach (var item in EnumerateAllSessionItems())
        {
            cancellationToken.ThrowIfCancellationRequested();

            IndexRecord indexRecord;
            try
            {
                indexRecord = GetOrBuildIndexRecord(item);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (FileNotFoundException)
            {
                continue;
            }
            catch (IOException)
            {
                // Skip files being written/locked by another process.
                continue;
            }
            catch (UnauthorizedAccessException)
            {
                continue;
            }
            catch
            {
                continue;
            }

            var sessionTimestamp = FirstNonEmpty(indexRecord.Summary.StartedAt, indexRecord.Summary.Mtime);
            var sessionUsage = string.Equals(item.SourceType, "claude_cli", StringComparison.Ordinal)
                ? BuildCliSessionUsageForCostSummary(item.Path)
                : indexRecord.Summary.Usage;
            if (sessionUsage is not null
                && TryParseLocalTimestamp(sessionTimestamp, out var sessionLocalTimestamp))
            {
                foreach (var group in groups)
                {
                    group.AddSessionUsage(sessionLocalTimestamp, sessionUsage);
                }
            }

            IEnumerable<SessionEventDto> usageEvents;
            try
            {
                usageEvents = string.Equals(item.SourceType, "claude_cli", StringComparison.Ordinal)
                    ? EnumerateCliTokenUsageEventsForCostSummary(item.Path)
                    : Array.Empty<SessionEventDto>();
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (FileNotFoundException)
            {
                continue;
            }
            catch (IOException)
            {
                continue;
            }
            catch (UnauthorizedAccessException)
            {
                continue;
            }
            catch
            {
                continue;
            }

            foreach (var @event in usageEvents)
            {
                if (!string.Equals(@event.Kind, "token_usage", StringComparison.Ordinal)
                    || @event.Usage is null
                    || !TryParseLocalTimestamp(@event.Timestamp, out var eventLocalTimestamp))
                {
                    continue;
                }

                foreach (var group in groups)
                {
                    group.AddTokenUsageEvent(eventLocalTimestamp, @event.Usage);
                }
            }
        }

        return new CostSummaryResponse
        {
            GeneratedAt = ToIsoLocal(DateTime.Now),
            TimeZoneId = TimeZoneInfo.Local.Id,
            ExchangeRate = exchangeRate,
            Groups = groups.Select(group => group.ToDto()).ToArray(),
        };
    }

    private bool TryGetCachedCostSummary(long pricingVersion, out CostSummaryResponse response)
    {
        var cached = _costSummaryCache;
        if (cached is not null
            && cached.PricingVersion == pricingVersion
            && DateTimeOffset.UtcNow - cached.BuiltAtUtc <= CostSummaryCacheTtl)
        {
            response = cached.Response;
            return true;
        }

        response = null!;
        return false;
    }

    public async Task<SessionListResponse> GetSessionsAsync(
        string? query,
        string? mode,
        string? sort,
        int? sessionLabelId,
        int? eventLabelId,
        bool forceRefreshSessionItems = false,
        CancellationToken cancellationToken = default)
    {
        var roots = GetRoots();
        var snapshot = await _labelStore.GetSnapshotAsync(cancellationToken);
        var normalizedMode = string.Equals(mode, "or", StringComparison.OrdinalIgnoreCase) ? "or" : "and";
        var normalizedSort = sort is "asc" or "updated" ? sort : "desc";
        var terms = ParseSearchQuery(query)
            .Select(NormalizeSearchText)
            .Where(term => !string.IsNullOrWhiteSpace(term))
            .ToArray();

        var sessions = new List<SessionSummaryDto>();
        foreach (var item in EnumerateAllSessionItems(forceRefreshSessionItems))
        {
            cancellationToken.ThrowIfCancellationRequested();
            IndexRecord record;
            try
            {
                record = GetOrBuildIndexRecord(item);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (FileNotFoundException)
            {
                continue;
            }
            catch
            {
                // Skip unreadable or malformed session files and continue scanning.
                continue;
            }

            if (terms.Length > 0 && !MatchesTerms(record.SearchText, terms, normalizedMode))
            {
                continue;
            }

            var sessionLabelIds = snapshot.SessionLabels.TryGetValue(record.Summary.Path, out var labelIds)
                ? labelIds
                : Array.Empty<int>();
            if (sessionLabelId.HasValue && !sessionLabelIds.Contains(sessionLabelId.Value))
            {
                continue;
            }

            if (eventLabelId.HasValue && !HasEventLabel(snapshot, record.Summary.Path, eventLabelId.Value))
            {
                continue;
            }

            sessions.Add(WithSessionLabelIds(record.Summary, sessionLabelIds));
        }

        var settings = _viewerSettings.GetSnapshot();
        IOrderedEnumerable<SessionSummaryDto> ordered = normalizedSort switch
        {
            "asc" => sessions
                .OrderBy(GetSessionSortKey, StringComparer.Ordinal)
                .ThenBy(session => session.Mtime, StringComparer.Ordinal),
            "updated" => sessions
                .OrderByDescending(session => session.Mtime, StringComparer.Ordinal)
                .ThenByDescending(GetSessionSortKey, StringComparer.Ordinal),
            _ => sessions
                .OrderByDescending(GetSessionSortKey, StringComparer.Ordinal)
                .ThenByDescending(session => session.Mtime, StringComparer.Ordinal),
        };

        var limitedSessions = ordered.Take(settings.SessionListMax).ToArray();
        return new SessionListResponse
        {
            Root = BuildRootSummaryText(roots),
            Roots = roots,
            Sessions = limitedSessions,
            TotalCount = limitedSessions.Length,
            Offset = 0,
            Limit = settings.SessionListMax,
            HasMore = false,
        };
    }

    public async Task<SessionListResponse> GetSessionsLiteAsync(
        string? sort,
        int? offset,
        int? limit,
        CancellationToken cancellationToken = default)
    {
        var roots = GetRoots();
        var settings = _viewerSettings.GetSnapshot();
        var snapshot = await _labelStore.GetSnapshotAsync(cancellationToken);
        var normalizedSort = sort is "asc" or "updated" ? sort : "desc";
        var allItems = EnumerateAllSessionItems();

        SessionItem[] limitedItems = allItems
            .Take(settings.SessionListMax)
            .ToArray();
        if (normalizedSort == "asc")
        {
            Array.Reverse(limitedItems);
        }

        var totalCount = limitedItems.Length;
        var normalizedOffset = Math.Clamp(offset ?? 0, 0, totalCount);
        var normalizedLimit = Math.Clamp(limit ?? settings.SessionListInitialLoadCount, 1, settings.SessionListMax);
        var pageItems = limitedItems
            .Skip(normalizedOffset)
            .Take(normalizedLimit)
            .ToArray();

        var sessions = new List<SessionSummaryDto>(pageItems.Length);
        foreach (var item in pageItems)
        {
            cancellationToken.ThrowIfCancellationRequested();

            IndexRecord record;
            try
            {
                record = GetOrBuildIndexRecord(item);
            }
            catch (FileNotFoundException)
            {
                continue;
            }

            var sessionLabelIds = snapshot.SessionLabels.TryGetValue(record.Summary.Path, out var labelIds)
                ? labelIds
                : Array.Empty<int>();
            sessions.Add(WithSessionLabelIds(record.Summary, sessionLabelIds));
        }

        return new SessionListResponse
        {
            Root = BuildRootSummaryText(roots),
            Roots = roots,
            Sessions = sessions,
            TotalCount = totalCount,
            Offset = normalizedOffset,
            Limit = normalizedLimit,
            HasMore = normalizedOffset + sessions.Count < totalCount,
        };
    }

    public async Task<SessionDetailResponse> GetSessionAsync(
        string? rawPath,
        string? rawSourceType,
        bool includeEvents,
        CancellationToken cancellationToken = default)
    {
        var item = ResolveSessionItem(rawPath, rawSourceType);
        var fileInfo = new FileInfo(item.Path);
        if (!fileInfo.Exists)
        {
            _cache.TryRemove(item.Path, out _);
            throw new FileNotFoundException("session file not found", item.Path);
        }

        var sessionVersion = BuildSessionVersion(fileInfo);
        var snapshot = await _labelStore.GetSnapshotAsync(cancellationToken);
        var exchangeRate = await _exchangeRates.GetUsdJpyRateAsync(cancellationToken);
        var indexRecord = GetOrBuildIndexRecord(item);
        var sessionPath = indexRecord.Summary.Path;
        var sessionLabelIds = snapshot.SessionLabels.TryGetValue(sessionPath, out var sIds) ? sIds : Array.Empty<int>();

        if (!includeEvents)
        {
            return new SessionDetailResponse
            {
                Session = WithSessionLabels(
                    indexRecord.Summary,
                    sessionLabelIds,
                    ResolveLabels(sessionLabelIds, snapshot.LabelById)),
                SessionVersion = sessionVersion,
                ExchangeRate = exchangeRate,
            };
        }

        var eventsData = GetOrBuildEvents(item);
        var labelsByEvent = snapshot.EventLabels.TryGetValue(sessionPath, out var eventMap)
            ? eventMap
            : null;

        return new SessionDetailResponse
        {
            Session = WithSessionLabels(
                indexRecord.Summary,
                sessionLabelIds,
                ResolveLabels(sessionLabelIds, snapshot.LabelById)),
            SessionVersion = sessionVersion,
            Events = eventsData.Events
                .Select(@event => WithEventLabels(
                    @event,
                    ResolveLabels(
                        labelsByEvent is not null && labelsByEvent.TryGetValue(@event.EventId, out var ids)
                            ? ids
                            : Array.Empty<int>(),
                        snapshot.LabelById)))
                .ToArray(),
            RawLineCount = eventsData.RawLineCount,
            ExchangeRate = exchangeRate,
        };
    }

    public SessionVersionResponse GetSessionVersion(string? rawPath, string? rawSourceType)
    {
        var item = ResolveSessionItem(rawPath, rawSourceType);
        var fileInfo = new FileInfo(item.Path);
        if (!fileInfo.Exists)
        {
            _cache.TryRemove(item.Path, out _);
            throw new FileNotFoundException("session file not found", item.Path);
        }

        return new SessionVersionResponse
        {
            Path = item.Path,
            SessionVersion = BuildSessionVersion(fileInfo),
        };
    }

    public async Task<LabelDto> SaveLabelAsync(SaveLabelRequest request, CancellationToken cancellationToken = default)
    {
        return await _labelStore.SaveLabelAsync(request.Id, request.Name, request.ColorValue, request.ColorFamily, cancellationToken);
    }

    public async Task DeleteLabelAsync(int id, CancellationToken cancellationToken = default)
    {
        await _labelStore.DeleteLabelAsync(id, cancellationToken);
    }

    public async Task AddSessionLabelAsync(SessionLabelMutationRequest request, CancellationToken cancellationToken = default)
    {
        var item = ResolveSessionItem(request.Path, null);
        if (request.LabelId is null)
        {
            throw new InvalidOperationException("label id is required");
        }

        await _labelStore.AddSessionLabelAsync(item.Path, request.LabelId.Value, cancellationToken);
    }

    public async Task RemoveSessionLabelAsync(SessionLabelMutationRequest request, CancellationToken cancellationToken = default)
    {
        var item = ResolveSessionItem(request.Path, null);
        if (request.LabelId is null)
        {
            throw new InvalidOperationException("label id is required");
        }

        await _labelStore.RemoveSessionLabelAsync(item.Path, request.LabelId.Value, cancellationToken);
    }

    public async Task AddEventLabelAsync(EventLabelMutationRequest request, CancellationToken cancellationToken = default)
    {
        var item = ResolveSessionItem(request.Path, null);
        if (request.LabelId is null || string.IsNullOrWhiteSpace(request.EventId))
        {
            throw new InvalidOperationException("label id and event id are required");
        }

        await _labelStore.AddEventLabelAsync(item.Path, request.EventId.Trim(), request.LabelId.Value, cancellationToken);
    }

    public async Task RemoveEventLabelAsync(EventLabelMutationRequest request, CancellationToken cancellationToken = default)
    {
        var item = ResolveSessionItem(request.Path, null);
        if (request.LabelId is null || string.IsNullOrWhiteSpace(request.EventId))
        {
            throw new InvalidOperationException("label id and event id are required");
        }

        await _labelStore.RemoveEventLabelAsync(item.Path, request.EventId.Trim(), request.LabelId.Value, cancellationToken);
    }

    private RootsDto GetRoots()
    {
        return new RootsDto
        {
            ClaudeCli = GetClaudeCliRoots(),
            ClaudeDesktop = GetClaudeDesktopRoots(),
        };
    }

    private IReadOnlyList<string> GetClaudeCliRoots()
    {
        if (_cliRoots is not null)
        {
            return _cliRoots;
        }

        var envRoots = ResolveRootsFromEnv();
        if (envRoots is not null)
        {
            _cliRoots = envRoots.Count > 0 ? envRoots : GetBundledSampleCliRoots();
            if (!HasAnyCliSessionFiles(_cliRoots))
            {
                _cliRoots = GetBundledSampleCliRoots();
            }

            return _cliRoots;
        }

        var candidates = new List<string>();
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var userProfile = Environment.GetEnvironmentVariable("USERPROFILE");
        var winHome = Environment.GetEnvironmentVariable("WIN_HOME");

        candidates.Add(CanonicalizePath(Path.Combine(home, ".claude", "projects")));
        if (!string.IsNullOrWhiteSpace(userProfile))
        {
            candidates.Add(CanonicalizePath(Path.Combine(userProfile, ".claude", "projects")));
        }

        if (!string.IsNullOrWhiteSpace(winHome))
        {
            candidates.Add(CanonicalizePath(Path.Combine(winHome, ".claude", "projects")));
        }

        var usersRoot = CanonicalizePath("/mnt/c/Users");
        if (Directory.Exists(usersRoot))
        {
            foreach (var directory in SafeEnumerateDirectories(usersRoot))
            {
                candidates.Add(CanonicalizePath(Path.Combine(directory, ".claude", "projects")));
            }
        }

        if (OperatingSystem.IsWindows())
        {
            candidates.AddRange(GetWslCliRootsOnWindows());
        }

        _cliRoots = ExistingOrUnique(candidates);
        if (!HasAnyCliSessionFiles(_cliRoots))
        {
            _cliRoots = GetBundledSampleCliRoots();
        }

        return _cliRoots;
    }

    private IReadOnlyList<string> GetClaudeDesktopRoots()
    {
        if (_desktopRoots is not null)
        {
            return _desktopRoots;
        }

        var candidates = new List<string>();
        var appData = Environment.GetEnvironmentVariable("APPDATA");
        var userProfile = Environment.GetEnvironmentVariable("USERPROFILE");
        var winHome = Environment.GetEnvironmentVariable("WIN_HOME");

        if (!string.IsNullOrWhiteSpace(appData))
        {
            candidates.Add(CanonicalizePath(Path.Combine(appData, "Claude", "IndexedDB")));
        }

        if (!string.IsNullOrWhiteSpace(userProfile))
        {
            candidates.Add(CanonicalizePath(Path.Combine(userProfile, "AppData", "Roaming", "Claude", "IndexedDB")));
        }

        if (!string.IsNullOrWhiteSpace(winHome))
        {
            candidates.Add(CanonicalizePath(Path.Combine(winHome, "AppData", "Roaming", "Claude", "IndexedDB")));
        }

        var usersRoot = CanonicalizePath("/mnt/c/Users");
        if (Directory.Exists(usersRoot))
        {
            foreach (var directory in SafeEnumerateDirectories(usersRoot))
            {
                candidates.Add(CanonicalizePath(Path.Combine(directory, "AppData", "Roaming", "Claude", "IndexedDB")));
            }
        }

        _desktopRoots = ExistingOrUnique(candidates);
        return _desktopRoots;
    }

    private IReadOnlyList<string>? ResolveRootsFromEnv()
    {
        var raw = Environment.GetEnvironmentVariable("CLAUDE_SESSIONS_DIR");
        if (string.IsNullOrWhiteSpace(raw))
        {
            raw = Environment.GetEnvironmentVariable("SESSIONS_DIR");
        }

        if (string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }

        var separators = raw.Contains(';', StringComparison.Ordinal)
            ? new[] { ';' }
            : new[] { Path.PathSeparator };
        var parts = raw
            .Split(separators, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(part => !string.IsNullOrWhiteSpace(part))
            .Select(CanonicalizePath)
            .ToArray();

        return ExistingOrUnique(parts);
    }

    private IReadOnlyList<string> GetBundledSampleCliRoots()
    {
        return ExistingOrUnique([
            CanonicalizePath(Path.Combine(_contentRootPath, "sample-data", "claude", "projects")),
        ]);
    }

    private static bool HasAnyCliSessionFiles(IReadOnlyList<string> roots)
    {
        if (roots.Count == 0)
        {
            return false;
        }

        var options = new EnumerationOptions
        {
            RecurseSubdirectories = true,
            IgnoreInaccessible = true,
            ReturnSpecialDirectories = false,
        };

        foreach (var root in roots)
        {
            if (!Directory.Exists(root))
            {
                continue;
            }

            using var enumerator = SafeEnumerateFiles(root, "*.jsonl", options).GetEnumerator();
            if (enumerator.MoveNext())
            {
                return true;
            }
        }

        return false;
    }

    private IReadOnlyList<string> GetWslCliRootsOnWindows()
    {
        if (_wslCliRootsOnWindows is not null)
        {
            return _wslCliRootsOnWindows;
        }

        if (!OperatingSystem.IsWindows())
        {
            _wslCliRootsOnWindows = [];
            return _wslCliRootsOnWindows;
        }

        var candidates = new List<string>();
        foreach (var distro in GetWslDistrosOnWindows())
        {
            var actualHome = GetWslHomeOnWindows(distro);
            if (!string.IsNullOrWhiteSpace(actualHome))
            {
                var actualHomeRoot = ToWslUncPath(distro, actualHome);
                if (!string.IsNullOrWhiteSpace(actualHomeRoot))
                {
                    candidates.Add(CanonicalizePath(Path.Combine(actualHomeRoot, ".claude", "projects")));
                }
            }

            var homeRoot = ToWslUncPath(distro, "/home");
            if (!string.IsNullOrWhiteSpace(homeRoot) && Directory.Exists(homeRoot))
            {
                foreach (var directory in SafeEnumerateDirectories(homeRoot))
                {
                    candidates.Add(CanonicalizePath(Path.Combine(directory, ".claude", "projects")));
                }
            }

            var rootHome = ToWslUncPath(distro, "/root");
            if (!string.IsNullOrWhiteSpace(rootHome))
            {
                candidates.Add(CanonicalizePath(Path.Combine(rootHome, ".claude", "projects")));
            }
        }

        _wslCliRootsOnWindows = ExistingOrUnique(candidates);
        return _wslCliRootsOnWindows;
    }

    private static IReadOnlyList<string> GetWslDistrosOnWindows()
    {
        if (!OperatingSystem.IsWindows())
        {
            return [];
        }

        var overrideValue = Environment.GetEnvironmentVariable("CLAUDE_WSL_DISTROS");
        if (!string.IsNullOrWhiteSpace(overrideValue))
        {
            return SplitSimpleList(overrideValue);
        }

        var output = RunCommandCapture("wsl.exe", "-l", "-q");
        return string.IsNullOrWhiteSpace(output)
            ? []
            : SplitSimpleList(output);
    }

    private static IReadOnlyList<string> SplitSimpleList(string raw)
    {
        return WhitespaceRegex.Replace(raw.Replace(';', '\n').Replace(',', '\n'), " ")
            .Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .SelectMany(line => line.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static string GetWslHomeOnWindows(string distro)
    {
        if (!OperatingSystem.IsWindows() || string.IsNullOrWhiteSpace(distro))
        {
            return string.Empty;
        }

        var output = RunCommandCapture("wsl.exe", "-d", distro, "sh", "-lc", "printf '%s' \"$HOME\"");
        return !string.IsNullOrWhiteSpace(output) && output.StartsWith("/", StringComparison.Ordinal)
            ? output
            : string.Empty;
    }

    private static string RunCommandCapture(params string[] args)
    {
        try
        {
            using var process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = args[0],
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                },
            };

            foreach (var arg in args.Skip(1))
            {
                process.StartInfo.ArgumentList.Add(arg);
            }

            process.Start();
            var output = process.StandardOutput.ReadToEnd();
            if (!process.WaitForExit(8000) || process.ExitCode != 0)
            {
                try
                {
                    process.Kill(entireProcessTree: true);
                }
                catch
                {
                    // Ignore cleanup failures.
                }

                return string.Empty;
            }

            return output.Replace("\0", string.Empty, StringComparison.Ordinal).Trim();
        }
        catch
        {
            return string.Empty;
        }
    }

    private IReadOnlyList<SessionItem> EnumerateAllSessionItems(bool forceRefresh = false)
    {
        var cliRoots = GetClaudeCliRoots()
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(CanonicalizePath)
            .Distinct(PathComparer)
            .OrderBy(root => root, PathComparer)
            .ToArray();
        var desktopRoots = GetClaudeDesktopRoots()
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(CanonicalizePath)
            .Distinct(PathComparer)
            .OrderBy(root => root, PathComparer)
            .ToArray();
        var cacheKey = string.Join("|",
            cliRoots.Select(root => $"cli:{root}")
                .Concat(desktopRoots.Select(root => $"desktop:{root}")));
        var now = DateTime.UtcNow;

        lock (_sessionItemsCacheLock)
        {
            if (!forceRefresh
                && _sessionItemsCache is not null
                && _sessionItemsCache.RootsKey == cacheKey
                && now - _sessionItemsCache.BuiltAtUtc <= SessionItemsCacheTtl)
            {
                return _sessionItemsCache.Items;
            }
        }

        var items = new Dictionary<string, SessionItem>(PathComparer);
        var options = new EnumerationOptions
        {
            RecurseSubdirectories = true,
            IgnoreInaccessible = true,
            ReturnSpecialDirectories = false,
        };

        foreach (var root in cliRoots)
        {
            if (!Directory.Exists(root))
            {
                continue;
            }

            foreach (var file in SafeEnumerateFiles(root, "*.jsonl", options))
            {
                var path = CanonicalizePath(file);
                items.TryAdd(path, new SessionItem("claude_cli", path, root));
            }
        }

        foreach (var root in desktopRoots)
        {
            if (!Directory.Exists(root))
            {
                continue;
            }

            foreach (var pattern in new[] { "*.ldb", "*.log", "MANIFEST-*" })
            {
                foreach (var file in SafeEnumerateFiles(root, pattern, options))
                {
                    var path = CanonicalizePath(file);
                    items.TryAdd(path, new SessionItem("claude_desktop", path, root));
                }
            }
        }

        var results = items.Values
            .OrderByDescending(item => SafeGetLastWriteTimeUtc(item.Path))
            .ThenBy(item => item.Path, PathComparer)
            .ToArray();

        lock (_sessionItemsCacheLock)
        {
            _sessionItemsCache = new SessionItemsCacheEntry(cacheKey, now, results);
        }

        return results;
    }

    private SessionItem ResolveSessionItem(string? rawPath, string? rawSourceType)
    {
        if (string.IsNullOrWhiteSpace(rawPath))
        {
            throw new InvalidOperationException("path is required");
        }

        var candidate = CanonicalizePath(rawPath);
        var requestedSource = rawSourceType is "claude_cli" or "claude_desktop" ? rawSourceType : string.Empty;
        var roots = new Dictionary<string, IReadOnlyList<string>>(StringComparer.Ordinal)
        {
            ["claude_cli"] = GetClaudeCliRoots(),
            ["claude_desktop"] = GetClaudeDesktopRoots(),
        };

        var sources = string.IsNullOrWhiteSpace(requestedSource)
            ? new[] { "claude_cli", "claude_desktop" }
            : new[] { requestedSource };

        foreach (var source in sources)
        {
            foreach (var root in roots[source])
            {
                if (!IsWithinRoot(candidate, root))
                {
                    continue;
                }

                if (!File.Exists(candidate))
                {
                    throw new FileNotFoundException("session file not found");
                }

                return new SessionItem(source, candidate, root);
            }
        }

        throw new InvalidOperationException("path is outside allowed roots");
    }

    private IndexRecord GetOrBuildIndexRecord(SessionItem item)
    {
        var fileInfo = new FileInfo(item.Path);
        if (!fileInfo.Exists)
        {
            _cache.TryRemove(item.Path, out _);
            throw new FileNotFoundException("session file not found", item.Path);
        }

        var signature = GetSignature(fileInfo);
        if (_cache.TryGetValue(item.Path, out var cached)
            && cached.Signature == signature
            && cached.IndexRecord is not null)
        {
            cached.LastAccessedTicks = Environment.TickCount64;
            return cached.IndexRecord;
        }

        var built = BuildIndexRecord(item, fileInfo);
        _cache[item.Path] = new SessionCacheEntry
        {
            Signature = signature,
            IndexRecord = built,
            EventsData = cached is not null && cached.Signature == signature ? cached.EventsData : null,
            ViewerSettingsVersion = cached?.ViewerSettingsVersion ?? 0,
            MaxEvents = cached?.MaxEvents ?? 0,
        };
        TrimCacheIfNeeded();
        return built;
    }

    private EventsData GetOrBuildEvents(SessionItem item)
    {
        var fileInfo = new FileInfo(item.Path);
        if (!fileInfo.Exists)
        {
            _cache.TryRemove(item.Path, out _);
            throw new FileNotFoundException("session file not found", item.Path);
        }

        var signature = GetSignature(fileInfo);
        var settings = _viewerSettings.GetSnapshot();
        if (_cache.TryGetValue(item.Path, out var cached)
            && cached.Signature == signature
            && cached.ViewerSettingsVersion == settings.Version
            && cached.MaxEvents == settings.SessionEventsMax
            && cached.EventsData is not null)
        {
            cached.LastAccessedTicks = Environment.TickCount64;
            return cached.EventsData;
        }

        var built = item.SourceType == "claude_cli"
            ? LoadCliEvents(item.Path, settings.SessionEventsMax)
            : LoadDesktopEvents(item.Path, settings.SessionEventsMax);
        _cache[item.Path] = new SessionCacheEntry
        {
            Signature = signature,
            IndexRecord = cached is not null && cached.Signature == signature ? cached.IndexRecord : null,
            EventsData = built,
            ViewerSettingsVersion = settings.Version,
            MaxEvents = settings.SessionEventsMax,
        };
        TrimCacheIfNeeded();
        return built;
    }

    private void TrimCacheIfNeeded()
    {
        if (_cache.Count <= MaxCacheEntries)
        {
            return;
        }

        var entries = _cache.ToArray();
        var scored = entries
            .Select(pair => (pair.Key, Ticks: pair.Value.LastAccessedTicks))
            .OrderBy(item => item.Ticks)
            .Take(entries.Length - MaxCacheEntries)
            .ToArray();

        foreach (var item in scored)
        {
            _cache.TryRemove(item.Key, out _);
        }
    }

    private IndexRecord BuildIndexRecord(SessionItem item, FileInfo fileInfo)
    {
        return item.SourceType == "claude_cli"
            ? BuildCliIndexRecord(item, fileInfo)
            : BuildDesktopIndexRecord(item, fileInfo);
    }

    private IndexRecord BuildCliIndexRecord(SessionItem item, FileInfo fileInfo)
    {
        var summary = new SessionSummaryDto
        {
            Id = Path.GetFileNameWithoutExtension(item.Path),
            Path = SessionPathKey(item.Path),
            RelativePath = SafeRelativePath(item.Path, item.Root),
            Source = "Claude Code CLI",
            SourceType = "claude_cli",
            Project = string.Empty,
            Mtime = ToIsoLocal(fileInfo.LastWriteTime),
            StartedAt = string.Empty,
            Cwd = string.Empty,
            Model = string.Empty,
            EffortLevel = string.Empty,
            FirstUserText = string.Empty,
            FirstRealUserText = string.Empty,
            MinEventTs = string.Empty,
            MaxEventTs = string.Empty,
        };
        var models = new List<string>();

        if (summary.RelativePath.Contains('/'))
        {
            summary = summary with { Project = summary.RelativePath.Split('/', 2)[0] };
        }
        else if (summary.RelativePath.Contains('\\'))
        {
            summary = summary with { Project = summary.RelativePath.Split('\\', 2)[0] };
        }

        var searchChunks = new List<string>();
        var searchLength = 0;
        var usageAccumulator = new UsageAccumulator();

        try
        {
            var rawLineCount = 0;
            foreach (var line in File.ReadLines(item.Path))
            {
                rawLineCount++;
                if (!TryParseJson(line, out var document))
                {
                    continue;
                }

                using (document!)
                {
                    var obj = document.RootElement;
                    var @event = BuildCliEvent(obj, rawLineCount);
                    var modelName = ExtractModelName(obj);
                    if (string.IsNullOrWhiteSpace(summary.StartedAt) && !string.IsNullOrWhiteSpace(@event.Timestamp))
                    {
                        summary = summary with { StartedAt = @event.Timestamp };
                    }

                    if (string.IsNullOrWhiteSpace(summary.Model))
                    {
                        summary = summary with
                        {
                            Model = modelName,
                        };
                    }

                    if (string.IsNullOrWhiteSpace(summary.EffortLevel) && !string.IsNullOrWhiteSpace(@event.EffortLevel))
                    {
                        summary = summary with { EffortLevel = @event.EffortLevel };
                    }

                    AddDistinctModel(models, summary.Model);
                    AddDistinctModel(models, modelName);

                    if (string.IsNullOrWhiteSpace(summary.Cwd))
                    {
                        summary = summary with { Cwd = GetString(obj, "cwd") };
                    }

                    summary = UpdateMinMaxEventTimestamps(summary, @event.Timestamp);
                    usageAccumulator.Add(@event.Usage);

                    if (@event.Kind == "message" && @event.Role == "user" && !string.IsNullOrWhiteSpace(@event.Text))
                    {
                        if (string.IsNullOrWhiteSpace(summary.FirstUserText))
                        {
                            summary = summary with { FirstUserText = CollapseNewlines(@event.Text, 180) };
                        }

                        if (string.IsNullOrWhiteSpace(summary.FirstRealUserText) && !IsSystemLabeledUserEvent(@event))
                        {
                            summary = summary with { FirstRealUserText = CollapseNewlines(@event.Text, 180) };
                        }
                    }

                    searchLength = AppendSearchChunk(searchChunks, @event.Text, searchLength, SearchTextLimit);
                    foreach (var label in @event.SystemLabels)
                    {
                        searchLength = AppendSearchChunk(searchChunks, label, searchLength, SearchTextLimit);
                    }
                }
            }
        }
        catch
        {
            // Keep the partial summary when a file is unreadable.
        }

        summary = summary with
        {
            Project = ProjectDisplayLabel(summary.Project, summary.Cwd, item.Root),
            FirstRealUserText = string.IsNullOrWhiteSpace(summary.FirstRealUserText) ? summary.FirstUserText : summary.FirstRealUserText,
            Models = models,
            Usage = usageAccumulator.ToUsage(),
        };

        var prefix = new[]
        {
            summary.RelativePath,
            summary.Project,
            summary.Cwd,
            summary.Source,
            summary.SourceType,
            summary.FirstUserText,
            summary.FirstRealUserText,
        };

        var searchText = string.Join(" ", prefix
            .Select(NormalizeSearchText)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Concat(searchChunks));

        return new IndexRecord(summary, searchText);
    }

    private IndexRecord BuildDesktopIndexRecord(SessionItem item, FileInfo fileInfo)
    {
        var summary = new SessionSummaryDto
        {
            Id = Path.GetFileName(item.Path),
            Path = SessionPathKey(item.Path),
            RelativePath = SafeRelativePath(item.Path, item.Root),
            Source = "Claude Desktop (IndexedDB/LevelDB)",
            SourceType = "claude_desktop",
            Project = "(desktop)",
            Mtime = ToIsoLocal(fileInfo.LastWriteTime),
            StartedAt = string.Empty,
            Cwd = string.Empty,
            Model = string.Empty,
            EffortLevel = string.Empty,
            FirstUserText = string.Empty,
            FirstRealUserText = string.Empty,
            MinEventTs = string.Empty,
            MaxEventTs = string.Empty,
        };

        if (string.Equals(Path.GetExtension(item.Path), ".log", StringComparison.OrdinalIgnoreCase))
        {
            var entries = ParseDesktopEntries(item.Path);
            if (entries.Count > 0)
            {
                var searchChunks = new List<string>();
                var searchLength = 0;
                foreach (var entry in entries)
                {
                    if (string.IsNullOrWhiteSpace(summary.StartedAt) && !string.IsNullOrWhiteSpace(entry.UpdatedAt))
                    {
                        summary = summary with { StartedAt = entry.UpdatedAt };
                    }

                    summary = UpdateMinMaxEventTimestamps(summary, entry.UpdatedAt);
                    if (string.IsNullOrWhiteSpace(summary.FirstUserText))
                    {
                        summary = summary with
                        {
                            FirstUserText = CollapseNewlines(entry.Text, 180),
                            FirstRealUserText = CollapseNewlines(entry.Text, 180),
                        };
                    }

                    searchLength = AppendSearchChunk(searchChunks, entry.Text, searchLength, SearchTextLimit);
                }

                var searchText = string.Join(" ", new[]
                {
                    NormalizeSearchText(summary.RelativePath),
                    NormalizeSearchText(summary.Source),
                    NormalizeSearchText(summary.SourceType),
                    NormalizeSearchText(summary.Project),
                }.Where(value => !string.IsNullOrWhiteSpace(value)).Concat(searchChunks));

                return new IndexRecord(summary, searchText);
            }
        }

        try
        {
            var raw = ReadRawBytes(item.Path, Math.Min(MaxDesktopScanBytes, Math.Max(256 * 1024, checked((int)Math.Min(fileInfo.Length, int.MaxValue)))));
            var objects = ExtractJsonObjectsFromBytes(raw, 40);
            var searchChunks = new List<string>();
            var searchLength = 0;

            if (objects.Count > 0)
            {
                foreach (var obj in objects)
                {
                    var timestamp = ExtractTimestamp(obj);
                    if (string.IsNullOrWhiteSpace(summary.StartedAt) && !string.IsNullOrWhiteSpace(timestamp))
                    {
                        summary = summary with { StartedAt = timestamp };
                    }

                    summary = UpdateMinMaxEventTimestamps(summary, timestamp);
                    var text = string.Join(" ", ExtractTextRecursive(obj)).Trim();
                    if (string.IsNullOrWhiteSpace(text))
                    {
                        continue;
                    }

                    if (string.IsNullOrWhiteSpace(summary.FirstUserText))
                    {
                        summary = summary with
                        {
                            FirstUserText = CollapseNewlines(text, 180),
                            FirstRealUserText = CollapseNewlines(text, 180),
                        };
                    }

                    searchLength = AppendSearchChunk(searchChunks, text, searchLength, SearchTextLimit);
                }
            }
            else
            {
                foreach (var snippet in ExtractReadableSnippets(raw, 10))
                {
                    if (string.IsNullOrWhiteSpace(summary.FirstUserText))
                    {
                        summary = summary with
                        {
                            FirstUserText = CollapseNewlines(snippet, 180),
                            FirstRealUserText = CollapseNewlines(snippet, 180),
                        };
                    }

                    searchLength = AppendSearchChunk(searchChunks, snippet, searchLength, SearchTextLimit);
                }
            }

            var searchText = string.Join(" ", new[]
            {
                NormalizeSearchText(summary.RelativePath),
                NormalizeSearchText(summary.Source),
                NormalizeSearchText(summary.SourceType),
                NormalizeSearchText(summary.Project),
            }.Where(value => !string.IsNullOrWhiteSpace(value)).Concat(searchChunks));

            return new IndexRecord(summary, searchText);
        }
        catch
        {
            return new IndexRecord(summary, string.Join(" ", new[]
            {
                NormalizeSearchText(summary.RelativePath),
                NormalizeSearchText(summary.Source),
                NormalizeSearchText(summary.SourceType),
                NormalizeSearchText(summary.Project),
            }.Where(value => !string.IsNullOrWhiteSpace(value))));
        }
    }

    private static EventsData LoadCliEvents(string path, int maxEvents)
    {
        var events = new List<SessionEventDto>();
        var rawLineCount = 0;
        foreach (var line in File.ReadLines(path))
        {
            rawLineCount++;
            if (!TryParseJson(line, out var document))
            {
                continue;
            }

            using (document!)
            {
                var @event = BuildCliEvent(document.RootElement, rawLineCount);
                events.Add(@event);
                var tokenUsageEvent = BuildCliTokenUsageEvent(@event, rawLineCount);
                if (tokenUsageEvent is not null)
                {
                    events.Add(tokenUsageEvent);
                }
            }

            if (events.Count >= maxEvents)
            {
                break;
            }
        }

        return new EventsData(events, rawLineCount);
    }

    private static IEnumerable<SessionEventDto> EnumerateCliTokenUsageEvents(string path)
    {
        var rawLineCount = 0;
        foreach (var line in File.ReadLines(path))
        {
            rawLineCount++;
            if (!TryParseJson(line, out var document))
            {
                continue;
            }

            using (document!)
            {
                var @event = BuildCliEvent(document.RootElement, rawLineCount);
                var tokenUsageEvent = BuildCliTokenUsageEvent(@event, rawLineCount);
                if (tokenUsageEvent is not null)
                {
                    yield return tokenUsageEvent;
                }
            }
        }
    }

    private static SessionEventDto BuildCliEvent(JsonElement obj, int rawLineCount)
    {
        var timestamp = ExtractTimestamp(obj);
        var type = GetString(obj, "type");
        var role = GuessRole(obj);
        var kind = "event";
        var text = string.Empty;
        var modelName = ExtractModelName(obj);
        var effortLevel = ExtractEffortLevel(obj);
        var usage = ExtractUsage(obj, modelName);

        if (type == "user")
        {
            if (obj.TryGetProperty("message", out var message) && IsToolResultMessage(message))
            {
                kind = "tool_result";
                role = "tool";
                text = ExtractClaudeMessageText(message);
            }
            else
            {
                kind = "message";
                role = "user";
                text = obj.TryGetProperty("message", out var value) ? ExtractClaudeMessageText(value) : string.Empty;
            }
        }
        else if (type == "assistant")
        {
            kind = "message";
            role = "assistant";
            text = obj.TryGetProperty("message", out var value) ? ExtractClaudeMessageText(value) : string.Empty;
        }
        else if (type == "queue-operation")
        {
            kind = "queue";
            role = "system";
            text = JoinWithNewline(GetString(obj, "operation"), GetString(obj, "content"));
        }
        else if (type == "progress")
        {
            kind = "progress";
            role = "system";
            text = ExtractClaudeProgressText(obj);
        }
        else if (type == "system")
        {
            kind = "system";
            role = "system";
            text = SerializeElement(obj);
        }
        else
        {
            if (obj.TryGetProperty("message", out var value))
            {
                text = ExtractClaudeMessageText(value);
            }

            if (string.IsNullOrWhiteSpace(text))
            {
                text = string.Join("\n", ExtractTextRecursive(obj)).Trim();
            }
        }

        if (string.IsNullOrWhiteSpace(text))
        {
            var serialized = SerializeElement(obj);
            text = serialized.Length > 1000 ? serialized[..1000] : serialized;
        }

        var systemLabels = new List<string>();
        if (kind == "message" && role == "user")
        {
            if (IsSkillsInstructionText(text))
            {
                systemLabels.Add("SKILLS");
            }

            if (IsContinuationSummaryText(text))
            {
                systemLabels.Add("CONTINUATION_SUMMARY");
            }

            if (IsTaskNotificationText(text))
            {
                systemLabels.Add("BACKGROUND_TASK");
            }
        }

        return new SessionEventDto
        {
            EventId = $"line-{rawLineCount}",
            Timestamp = timestamp,
            Kind = kind,
            Role = role,
            Text = text,
            Model = modelName,
            EffortLevel = effortLevel,
            SystemLabels = systemLabels,
            Usage = usage,
        };
    }

    private static SessionEventDto? BuildCliTokenUsageEvent(SessionEventDto sourceEvent, int rawLineCount)
    {
        if (sourceEvent.Usage is null)
        {
            return null;
        }

        return new SessionEventDto
        {
            EventId = $"line-{rawLineCount}-usage",
            Timestamp = sourceEvent.Timestamp,
            Kind = "token_usage",
            Role = "system",
            Model = sourceEvent.Model,
            EffortLevel = sourceEvent.EffortLevel,
            Usage = sourceEvent.Usage,
        };
    }

    private UsageMetricsDto? BuildCliSessionUsageForCostSummary(string path)
    {
        var usageAccumulator = new UsageAccumulator();
        foreach (var line in File.ReadLines(path))
        {
            if (!TryParseJson(line, out var document))
            {
                continue;
            }

            using (document!)
            {
                var root = document.RootElement;
                var usage = ExtractUsageForCostSummary(root, ExtractModelName(root));
                usageAccumulator.Add(usage);
            }
        }

        return usageAccumulator.ToUsage();
    }

    private IEnumerable<SessionEventDto> EnumerateCliTokenUsageEventsForCostSummary(string path)
    {
        var rawLineCount = 0;
        foreach (var line in File.ReadLines(path))
        {
            rawLineCount++;
            if (!TryParseJson(line, out var document))
            {
                continue;
            }

            using (document!)
            {
                var root = document.RootElement;
                var usage = ExtractUsageForCostSummary(root, ExtractModelName(root));
                if (usage is null)
                {
                    continue;
                }

                yield return new SessionEventDto
                {
                    EventId = $"line-{rawLineCount}-usage",
                    Timestamp = ExtractTimestamp(root),
                    Kind = "token_usage",
                    Role = "system",
                    Model = ExtractModelName(root),
                    EffortLevel = ExtractEffortLevel(root),
                    Usage = usage,
                };
            }
        }
    }

    private static EventsData LoadDesktopEvents(string path, int maxEvents)
    {
        var events = new List<SessionEventDto>();

        if (string.Equals(Path.GetExtension(path), ".log", StringComparison.OrdinalIgnoreCase))
        {
            var entries = ParseDesktopEntries(path);
            if (entries.Count > 0)
            {
                events.Add(new SessionEventDto
                {
                    EventId = "notice-0",
                    Kind = "notice",
                    Role = "system",
                    Text = "Claude Desktop の IndexedDB(LevelDB) から chat-draft エントリを解析しました。 これらは送信前の下書きメッセージです。送信済み会話は claude.ai サーバー側に保存されています。",
                });

                var index = 0;
                foreach (var entry in entries)
                {
                    index++;
                    var parts = entry.IdbKey.Split(':');
                    var conversationLabel = parts.Length >= 3 ? parts[^1] : entry.IdbKey;
                    var attachmentNote = entry.AttachCount > 0 ? $"  [添付 {entry.AttachCount} 件]" : string.Empty;
                    events.Add(new SessionEventDto
                    {
                        EventId = $"entry-{index}",
                        Timestamp = entry.UpdatedAt,
                        Kind = "message",
                        Role = "user",
                        Text = $"[{conversationLabel}]\n{entry.Text}{attachmentNote}",
                    });

                    if (events.Count >= maxEvents)
                    {
                        break;
                    }
                }

                return new EventsData(events, entries.Count);
            }
        }

        var fileInfo = new FileInfo(path);
        var raw = ReadRawBytes(path, Math.Min(MaxDesktopScanBytes, Math.Max(256 * 1024, checked((int)Math.Min(fileInfo.Length, int.MaxValue)))));
        var objects = ExtractJsonObjectsFromBytes(raw, maxEvents);
        if (objects.Count > 0)
        {
            var index = 0;
            foreach (var obj in objects)
            {
                var text = string.Join("\n", ExtractTextRecursive(obj)).Trim();
                if (string.IsNullOrWhiteSpace(text))
                {
                    continue;
                }

                events.Add(new SessionEventDto
                {
                    EventId = $"snippet-{index}",
                    Timestamp = ExtractTimestamp(obj),
                    Kind = "snippet",
                    Role = GuessRole(obj),
                    Text = text.Length > 4000 ? text[..4000] : text,
                });
                index++;
                if (events.Count >= maxEvents)
                {
                    break;
                }
            }
        }
        else
        {
            var index = 0;
            foreach (var snippet in ExtractReadableSnippets(raw, 800))
            {
                events.Add(new SessionEventDto
                {
                    EventId = $"snippet-{index}",
                    Kind = "snippet",
                    Role = "system",
                    Text = snippet,
                });
                index++;
                if (events.Count >= maxEvents)
                {
                    break;
                }
            }
        }

        events.Insert(0, new SessionEventDto
        {
            EventId = "notice-0",
            Kind = "notice",
            Role = "system",
            Text = "Claude Desktop の IndexedDB(LevelDB) はバイナリ形式のため、ここでは文字列/JSONスニペット抽出で表示しています。 完全な履歴復元ではありません。",
        });

        return new EventsData(events.Take(maxEvents).ToArray(), events.Count);
    }

    private static List<DesktopEntry> ParseDesktopEntries(string path)
    {
        var fileInfo = new FileInfo(path);
        var raw = ReadRawBytes(path, Math.Min(MaxDesktopScanBytes, checked((int)Math.Min(fileInfo.Length, int.MaxValue))));
        var rawEntries = ParseLevelDbLog(raw);
        if (rawEntries.Count == 0)
        {
            return [];
        }

        var latest = new Dictionary<string, LevelDbEntry>(StringComparer.Ordinal);
        foreach (var entry in rawEntries)
        {
            latest[Convert.ToBase64String(entry.Key)] = entry;
        }

        var results = new List<DesktopEntry>();
        foreach (var entry in latest.Values)
        {
            var idbKey = DecodeIndexedDbStringKey(entry.Key);
            if (string.IsNullOrWhiteSpace(idbKey))
            {
                continue;
            }

            if (!TryExtractJsonFromValue(entry.Value, out var obj))
            {
                continue;
            }

            var updatedAt = string.Empty;
            if (TryGetProperty(obj, "updatedAt", out var updatedAtValue))
            {
                updatedAt = IsoFromTs(updatedAtValue);
            }

            var state = TryGetProperty(obj, "state", out var stateValue) && stateValue.ValueKind == JsonValueKind.Object
                ? stateValue
                : obj;
            JsonElement editorState = default;
            var hasEditorState = TryGetProperty(state, "tipTapEditorState", out editorState)
                || TryGetProperty(state, "editorState", out editorState);

            var texts = new List<string>();
            if (hasEditorState)
            {
                ExtractTipTapTexts(editorState, texts);
            }

            if (texts.Count == 0)
            {
                texts.AddRange(ExtractTextRecursive(obj));
            }

            var draftText = string.Concat(texts).Trim();
            if (string.IsNullOrWhiteSpace(draftText))
            {
                continue;
            }

            var attachCount = CountItems(state, "attachments") + CountItems(state, "files");
            results.Add(new DesktopEntry(idbKey, draftText, updatedAt, attachCount));
        }

        results.Sort(static (left, right) => string.CompareOrdinal(left.UpdatedAt, right.UpdatedAt));
        return results;
    }

    private static List<LevelDbEntry> ParseLevelDbLog(byte[] raw)
    {
        const int blockSize = 32_768;
        const int headerSize = 7;
        var records = new List<byte[]>();
        var fragment = Array.Empty<byte>();
        var position = 0;

        while (position < raw.Length)
        {
            var blockOffset = position % blockSize;
            if (blockSize - blockOffset < headerSize)
            {
                position += blockSize - blockOffset;
                continue;
            }

            if (position + headerSize > raw.Length)
            {
                break;
            }

            var length = BitConverter.ToUInt16(raw, position + 4);
            var recordType = raw[position + 6];
            position += headerSize;

            if (length == 0 && recordType == 0)
            {
                position = ((position - 1) / blockSize + 1) * blockSize;
                continue;
            }

            if (position + length > raw.Length)
            {
                break;
            }

            var data = raw[position..(position + length)];
            position += length;

            switch (recordType)
            {
                case 1:
                    records.Add(data);
                    break;
                case 2:
                    fragment = data;
                    break;
                case 3:
                    fragment = fragment.Length == 0 ? data : fragment.Concat(data).ToArray();
                    break;
                case 4:
                    records.Add(fragment.Length == 0 ? data : fragment.Concat(data).ToArray());
                    fragment = Array.Empty<byte>();
                    break;
            }
        }

        var entries = new List<LevelDbEntry>();
        foreach (var record in records)
        {
            if (record.Length < 12)
            {
                continue;
            }

            var count = BitConverter.ToUInt32(record, 8);
            var positionInRecord = 12;
            for (var index = 0; index < count; index++)
            {
                if (positionInRecord >= record.Length)
                {
                    break;
                }

                var valueType = record[positionInRecord];
                positionInRecord++;
                var keyLength = ReadVarint(record, ref positionInRecord);
                if (keyLength < 0 || positionInRecord + keyLength > record.Length)
                {
                    break;
                }

                var key = record[positionInRecord..(positionInRecord + keyLength)];
                positionInRecord += keyLength;

                if (valueType != 1)
                {
                    continue;
                }

                var valueLength = ReadVarint(record, ref positionInRecord);
                if (valueLength < 0 || positionInRecord + valueLength > record.Length)
                {
                    break;
                }

                var value = record[positionInRecord..(positionInRecord + valueLength)];
                positionInRecord += valueLength;
                entries.Add(new LevelDbEntry(key, value));
            }
        }

        return entries;
    }

    private static int ReadVarint(byte[] data, ref int position)
    {
        var result = 0;
        var shift = 0;
        while (position < data.Length)
        {
            var value = data[position];
            position++;
            result |= (value & 0x7F) << shift;
            if ((value & 0x80) == 0)
            {
                return result;
            }

            shift += 7;
        }

        return -1;
    }

    private static string DecodeIndexedDbStringKey(byte[] keyBytes)
    {
        if (keyBytes.Length < 8)
        {
            return string.Empty;
        }

        var slice = keyBytes[6..];
        if (slice.Length < 4)
        {
            return string.Empty;
        }

        try
        {
            var text = Encoding.BigEndianUnicode.GetString(slice);
            if (text.Length < 3)
            {
                return string.Empty;
            }

            var printable = text.All(ch => !char.IsControl(ch) || ch is ' ' or '\n' or '\t');
            if (!printable)
            {
                return string.Empty;
            }

            var asciiCount = text.Count(ch => ch < 128);
            return asciiCount >= text.Length / 2.0
                ? text
                : string.Empty;
        }
        catch
        {
            return string.Empty;
        }
    }

    private static bool TryExtractJsonFromValue(byte[] value, out JsonElement obj)
    {
        obj = default;
        var start = -1;
        for (var index = 0; index + 1 < value.Length; index++)
        {
            if (value[index] == 0x7B && value[index + 1] == 0x00)
            {
                start = index;
                break;
            }
        }

        if (start < 0)
        {
            return false;
        }

        var text = Encoding.Unicode.GetString(value, start, value.Length - start);
        if (!text.StartsWith('{'))
        {
            return false;
        }

        var depth = 0;
        var inString = false;
        var escaped = false;
        var end = -1;
        for (var index = 0; index < text.Length; index++)
        {
            var ch = text[index];
            if (inString)
            {
                if (escaped)
                {
                    escaped = false;
                }
                else if (ch == '\\')
                {
                    escaped = true;
                }
                else if (ch == '"')
                {
                    inString = false;
                }

                continue;
            }

            if (ch == '"')
            {
                inString = true;
            }
            else if (ch == '{')
            {
                depth++;
            }
            else if (ch == '}')
            {
                depth--;
                if (depth == 0)
                {
                    end = index + 1;
                    break;
                }
            }
        }

        if (end < 0)
        {
            return false;
        }

        try
        {
            using var document = JsonDocument.Parse(text[..end]);
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                return false;
            }

            obj = document.RootElement.Clone();
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static List<JsonElement> ExtractJsonObjectsFromBytes(byte[] raw, int limit)
    {
        var results = new List<JsonElement>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var text in DecodeRawTextCandidates(raw))
        {
            foreach (var obj in ExtractJsonObjectsFromText(text, limit))
            {
                var signature = SerializeElement(obj);
                if (signature.Length > 400)
                {
                    signature = signature[..400];
                }

                if (!seen.Add(signature))
                {
                    continue;
                }

                results.Add(obj);
                if (results.Count >= limit)
                {
                    return results;
                }
            }
        }

        return results;
    }

    private static IEnumerable<string> DecodeRawTextCandidates(byte[] raw)
    {
        if (raw.Length == 0)
        {
            yield break;
        }

        yield return Encoding.UTF8.GetString(raw);
        yield return Encoding.Unicode.GetString(raw);
    }

    private static List<JsonElement> ExtractJsonObjectsFromText(string text, int limit)
    {
        var results = new List<JsonElement>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var chunk in ExtractJsonCandidateStrings(text, limit * 6))
        {
            if (!chunk.Contains("\"text\"", StringComparison.Ordinal)
                && !chunk.Contains("\"content\"", StringComparison.Ordinal)
                && !chunk.Contains("\"prompt\"", StringComparison.Ordinal)
                && !chunk.Contains("\"message\"", StringComparison.Ordinal))
            {
                continue;
            }

            var key = chunk.Length > 400 ? chunk[..400] : chunk;
            if (!seen.Add(key))
            {
                continue;
            }

            try
            {
                using var document = JsonDocument.Parse(chunk);
                if (document.RootElement.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }

                results.Add(document.RootElement.Clone());
                if (results.Count >= limit)
                {
                    break;
                }
            }
            catch
            {
                // Ignore malformed fragments.
            }
        }

        return results;
    }

    private static List<string> ExtractJsonCandidateStrings(string text, int limit)
    {
        var results = new List<string>();
        var index = 0;
        while (index < text.Length && results.Count < limit)
        {
            if (text[index] != '{')
            {
                index++;
                continue;
            }

            var start = index;
            var depth = 0;
            var inString = false;
            var escaped = false;
            var cursor = index;
            while (cursor < text.Length)
            {
                var ch = text[cursor];
                if (inString)
                {
                    if (escaped)
                    {
                        escaped = false;
                    }
                    else if (ch == '\\')
                    {
                        escaped = true;
                    }
                    else if (ch == '"')
                    {
                        inString = false;
                    }
                }
                else
                {
                    if (ch == '"')
                    {
                        inString = true;
                    }
                    else if (ch == '{')
                    {
                        depth++;
                    }
                    else if (ch == '}')
                    {
                        depth--;
                        if (depth == 0)
                        {
                            var chunk = text[start..(cursor + 1)];
                            if (chunk.Length is >= 24 and <= 200_000)
                            {
                                results.Add(chunk);
                            }

                            break;
                        }
                    }
                }

                cursor++;
            }

            index = cursor > index ? cursor + 1 : index + 1;
        }

        return results;
    }

    private static List<string> ExtractReadableSnippets(byte[] raw, int limit)
    {
        var snippets = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var text in DecodeRawTextCandidates(raw))
        {
            foreach (Match match in ReadableSnippetRegex.Matches(text))
            {
                var snippet = match.Value.Trim();
                if (snippet.Length < 24)
                {
                    continue;
                }

                if (snippet.Contains("IndexedDB", StringComparison.Ordinal)
                    || snippet.Contains("LEVELDB", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var key = snippet.Length > 160 ? snippet[..160] : snippet;
                if (!seen.Add(key))
                {
                    continue;
                }

                snippets.Add(snippet);
                if (snippets.Count >= limit)
                {
                    return snippets;
                }
            }
        }

        return snippets;
    }

    private static void ExtractTipTapTexts(JsonElement node, List<string> texts)
    {
        if (node.ValueKind == JsonValueKind.Object)
        {
            if (GetString(node, "type") == "text")
            {
                var text = GetString(node, "text");
                if (!string.IsNullOrWhiteSpace(text))
                {
                    texts.Add(text);
                }
            }

            if (TryGetProperty(node, "content", out var content) && content.ValueKind == JsonValueKind.Array)
            {
                foreach (var child in content.EnumerateArray())
                {
                    ExtractTipTapTexts(child, texts);
                }
            }

            return;
        }

        if (node.ValueKind == JsonValueKind.Array)
        {
            foreach (var child in node.EnumerateArray())
            {
                ExtractTipTapTexts(child, texts);
            }
        }
    }

    private static IEnumerable<string> ExtractTextRecursive(JsonElement element)
    {
        var texts = new List<string>();
        ExtractTextRecursive(element, texts);
        return texts;
    }

    private static void ExtractTextRecursive(JsonElement element, List<string> texts)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.String:
            {
                var value = element.GetString()?.Trim();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    texts.Add(value);
                }

                break;
            }

            case JsonValueKind.Array:
                foreach (var item in element.EnumerateArray())
                {
                    ExtractTextRecursive(item, texts);
                }

                break;

            case JsonValueKind.Object:
            {
                var consumed = new HashSet<string>(StringComparer.Ordinal);
                foreach (var key in TextKeys)
                {
                    if (TryGetProperty(element, key, out var value))
                    {
                        consumed.Add(key);
                        ExtractTextRecursive(value, texts);
                    }
                }

                foreach (var property in element.EnumerateObject())
                {
                    if (consumed.Contains(property.Name) || SkipRecursiveKeys.Contains(property.Name))
                    {
                        continue;
                    }

                    ExtractTextRecursive(property.Value, texts);
                }

                break;
            }
        }
    }

    private static string GuessRole(JsonElement obj)
    {
        if (obj.ValueKind != JsonValueKind.Object)
        {
            return "system";
        }

        if (TryGetProperty(obj, "message", out var message) && message.ValueKind == JsonValueKind.Object)
        {
            var messageRole = NormalizeRole(GetString(message, "role"));
            if (!string.IsNullOrWhiteSpace(messageRole))
            {
                return messageRole;
            }
        }

        foreach (var key in new[] { "role", "sender", "author" })
        {
            var role = NormalizeRole(GetString(obj, key));
            if (!string.IsNullOrWhiteSpace(role))
            {
                return role;
            }
        }

        return NormalizeRole(GetString(obj, "type")) switch
        {
            "user" => "user",
            "assistant" => "assistant",
            "system" => "system",
            _ => "system",
        };
    }

    private static string NormalizeRole(string role)
    {
        return role.Trim().ToLowerInvariant() switch
        {
            "user" or "human" => "user",
            "assistant" or "claude" or "ai" => "assistant",
            "developer" or "dev" => "developer",
            "system" => "system",
            "human_message" => "user",
            "assistant_message" => "assistant",
            "system_message" => "system",
            _ => string.Empty,
        };
    }

    private static string ExtractClaudeMessageText(JsonElement message)
    {
        if (message.ValueKind == JsonValueKind.String)
        {
            return message.GetString()?.Trim() ?? string.Empty;
        }

        if (message.ValueKind != JsonValueKind.Object)
        {
            return string.Empty;
        }

        if (TryGetProperty(message, "content", out var content) && content.ValueKind == JsonValueKind.String)
        {
            return content.GetString()?.Trim() ?? string.Empty;
        }

        var chunks = new List<string>();
        if (TryGetProperty(message, "content", out content) && content.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in content.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object)
                {
                    if (item.ValueKind == JsonValueKind.String)
                    {
                        var text = item.GetString()?.Trim();
                        if (!string.IsNullOrWhiteSpace(text))
                        {
                            chunks.Add(text);
                        }
                    }

                    continue;
                }

                var itemType = GetString(item, "type");
                if (itemType == "text")
                {
                    var text = GetString(item, "text");
                    if (!string.IsNullOrWhiteSpace(text))
                    {
                        chunks.Add(text.Trim());
                    }
                }
                else if (itemType == "thinking")
                {
                    continue;
                }
                else if (itemType == "tool_use")
                {
                    var name = GetString(item, "name");
                    var args = TryGetProperty(item, "input", out var input)
                        ? input.ValueKind is JsonValueKind.Object or JsonValueKind.Array
                            ? SerializeElement(input, true)
                            : GetElementText(input)
                        : string.Empty;
                    var toolUseText = $"[tool_use] {name}\n{args}".Trim();
                    if (!string.IsNullOrWhiteSpace(toolUseText))
                    {
                        chunks.Add(toolUseText);
                    }
                }
                else if (itemType == "tool_result")
                {
                    var resultText = TryGetProperty(item, "content", out var itemContent)
                        ? string.Join("\n", ExtractTextRecursive(itemContent))
                        : string.Empty;
                    if (!string.IsNullOrWhiteSpace(resultText))
                    {
                        chunks.Add($"[tool_result] {resultText.Trim()}");
                    }
                }
                else
                {
                    var otherText = string.Join("\n", ExtractTextRecursive(item)).Trim();
                    if (!string.IsNullOrWhiteSpace(otherText))
                    {
                        chunks.Add(otherText);
                    }
                }
            }
        }

        if (chunks.Count > 0)
        {
            return string.Join("\n", chunks).Trim();
        }

        return string.Join("\n", ExtractTextRecursive(message)).Trim();
    }

    private static bool IsToolResultMessage(JsonElement message)
    {
        if (message.ValueKind != JsonValueKind.Object
            || !TryGetProperty(message, "content", out var content)
            || content.ValueKind != JsonValueKind.Array)
        {
            return false;
        }

        foreach (var item in content.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.Object && GetString(item, "type") == "tool_result")
            {
                return true;
            }
        }

        return false;
    }

    private static string ExtractClaudeProgressText(JsonElement obj)
    {
        if (!TryGetProperty(obj, "data", out var data) || data.ValueKind != JsonValueKind.Object)
        {
            return string.Empty;
        }

        return GetString(data, "type") switch
        {
            "mcp_progress" => $"mcp_progress status={GetString(data, "status")} server={GetString(data, "serverName")} tool={GetString(data, "toolName")} elapsed={GetString(data, "elapsedTimeMs")}".Trim(),
            "hook_progress" => $"hook_progress event={GetString(data, "hookEvent")} name={GetString(data, "hookName")} command={GetString(data, "command")}".Trim(),
            _ => SerializeElement(data),
        };
    }

    private static string ExtractModelName(JsonElement obj)
    {
        var messageModel = TryGetProperty(obj, "message", out var message)
            ? FirstNonEmpty(
                GetString(message, "model"),
                GetString(message, "model_name"),
                GetString(message, "modelName"))
            : string.Empty;

        return FirstNonEmpty(
            GetString(obj, "model"),
            GetString(obj, "model_name"),
            GetString(obj, "modelName"),
            messageModel);
    }

    private static string ExtractEffortLevel(JsonElement obj)
    {
        var messageEffort = TryGetProperty(obj, "message", out var message)
            ? FirstNonEmpty(
                GetString(message, "effort_level"),
                GetString(message, "effort"),
                GetString(message, "reasoning_effort"),
                TryGetNestedString(message, "metadata", "effort_level"),
                TryGetNestedString(message, "metadata", "effort"),
                TryGetNestedString(message, "metadata", "reasoning_effort"))
            : string.Empty;

        return FirstNonEmpty(
            GetString(obj, "effort_level"),
            GetString(obj, "effort"),
            GetString(obj, "reasoning_effort"),
            TryGetNestedString(obj, "collaboration_mode", "settings", "effort_level"),
            TryGetNestedString(obj, "collaboration_mode", "settings", "effort"),
            TryGetNestedString(obj, "collaboration_mode", "settings", "reasoning_effort"),
            messageEffort);
    }

    private static UsageMetricsDto? ExtractUsage(JsonElement obj, string modelName)
    {
        if (!TryGetProperty(obj, "message", out var message)
            || message.ValueKind != JsonValueKind.Object
            || !TryGetProperty(message, "usage", out var usage)
            || usage.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var inputTokens = GetInt64(usage, "input_tokens");
        var outputTokens = GetInt64(usage, "output_tokens");
        var cacheCreationTokens = GetInt64(usage, "cache_creation_input_tokens");
        var cacheReadTokens = GetInt64(usage, "cache_read_input_tokens");
        var costUsd = TryExtractCostUsd(obj, usage);

        if (inputTokens == 0
            && outputTokens == 0
            && cacheCreationTokens == 0
            && cacheReadTokens == 0
            && costUsd is null)
        {
            return null;
        }

        return CreateUsageMetrics(
            inputTokens,
            outputTokens,
            cacheCreationTokens,
            cacheReadTokens,
            costUsd);
    }

    private UsageMetricsDto? ExtractUsageForCostSummary(JsonElement obj, string modelName)
    {
        if (!TryGetProperty(obj, "message", out var message)
            || message.ValueKind != JsonValueKind.Object
            || !TryGetProperty(message, "usage", out var usage)
            || usage.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var inputTokens = GetInt64(usage, "input_tokens");
        var outputTokens = GetInt64(usage, "output_tokens");
        var cacheCreationTokens = GetInt64(usage, "cache_creation_input_tokens");
        var cacheReadTokens = GetInt64(usage, "cache_read_input_tokens");
        var costBreakdown = _modelPricing.TryCalculateCostBreakdownUsd(
            modelName,
            inputTokens,
            outputTokens,
            cacheCreationTokens,
            cacheReadTokens);
        var costUsd = costBreakdown?.TotalCostUsd
            ?? TryExtractCostUsd(obj, usage);

        if (inputTokens == 0
            && outputTokens == 0
            && cacheCreationTokens == 0
            && cacheReadTokens == 0
            && costUsd is null)
        {
            return null;
        }

        return CreateUsageMetrics(
            inputTokens,
            outputTokens,
            cacheCreationTokens,
            cacheReadTokens,
            costUsd,
            costBreakdown?.InputCostUsd,
            costBreakdown?.CacheCreationCostUsd,
            costBreakdown?.CacheReadCostUsd,
            costBreakdown?.OutputCostUsd);
    }

    private static string ExtractTimestamp(JsonElement obj)
    {
        if (obj.ValueKind != JsonValueKind.Object)
        {
            return string.Empty;
        }

        foreach (var key in new[] { "timestamp", "time", "created_at", "createdAt", "ts" })
        {
            if (TryGetProperty(obj, key, out var value))
            {
                var parsed = IsoFromTs(value);
                if (!string.IsNullOrWhiteSpace(parsed))
                {
                    return parsed;
                }
            }
        }

        return string.Empty;
    }

    private static string IsoFromTs(JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Number:
                return IsoFromNumericTs(value.TryGetInt64(out var whole) ? whole : value.GetDouble());

            case JsonValueKind.String:
            {
                var text = value.GetString()?.Trim() ?? string.Empty;
                if (string.IsNullOrWhiteSpace(text))
                {
                    return string.Empty;
                }

                if (Regex.IsMatch(text, @"^\d{10,16}$") && long.TryParse(text, out var numeric))
                {
                    return IsoFromNumericTs(numeric);
                }

                return text;
            }

            default:
                return string.Empty;
        }
    }

    private static string IsoFromNumericTs(double value)
    {
        try
        {
            if (value > 1_000_000_000_000)
            {
                return DateTimeOffset.FromUnixTimeMilliseconds((long)value).LocalDateTime.ToString("s");
            }

            return DateTimeOffset.FromUnixTimeSeconds((long)value).LocalDateTime.ToString("s");
        }
        catch
        {
            return string.Empty;
        }
    }

    private static bool IsSkillsInstructionText(string text)
    {
        return text.AsSpan().TrimStart().ToString().StartsWith("base directory for this skill:", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsContinuationSummaryText(string text)
    {
        var normalized = NormalizeInlineText(text);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return false;
        }

        return normalized.Contains("this session is being continued from a previous conversation that ran out of context.", StringComparison.Ordinal)
            && (normalized.Contains("the summary below covers the earlier portion of the conversation.", StringComparison.Ordinal)
                || normalized.Contains("summary:", StringComparison.Ordinal));
    }

    private static bool IsTaskNotificationText(string text)
    {
        return text.AsSpan().TrimStart().ToString().StartsWith("<task-notification>", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsSystemLabeledUserEvent(SessionEventDto @event)
    {
        return @event.Kind == "message"
            && @event.Role == "user"
            && (@event.SystemLabels.Contains("SKILLS")
                || @event.SystemLabels.Contains("CONTINUATION_SUMMARY")
                || @event.SystemLabels.Contains("BACKGROUND_TASK"));
    }

    private static string NormalizeSearchText(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return string.Empty;
        }

        return NormalizeInlineText(text.Replace('\\', '/')).ToLowerInvariant();
    }

    private static string NormalizeInlineText(string text)
    {
        return WhitespaceRegex.Replace(text, " ").Trim();
    }

    private static IReadOnlyList<string> ParseSearchQuery(string? query)
    {
        if (string.IsNullOrWhiteSpace(query))
        {
            return [];
        }

        var terms = new List<string>();
        var current = new StringBuilder();
        var inQuote = false;
        var quoteChar = '\0';
        for (var index = 0; index < query.Length; index++)
        {
            var ch = query[index];
            if (inQuote)
            {
                if (ch == quoteChar)
                {
                    if (current.Length > 0)
                    {
                        terms.Add(current.ToString());
                        current.Clear();
                    }

                    inQuote = false;
                }
                else if (ch == '\\' && index + 1 < query.Length)
                {
                    var next = query[index + 1];
                    if (next == quoteChar || next == '\\')
                    {
                        current.Append(next);
                        index++;
                    }
                    else
                    {
                        current.Append(ch);
                    }
                }
                else
                {
                    current.Append(ch);
                }

                continue;
            }

            if (ch is '"' or '\'')
            {
                if (current.Length > 0)
                {
                    terms.Add(current.ToString());
                    current.Clear();
                }

                inQuote = true;
                quoteChar = ch;
            }
            else if (char.IsWhiteSpace(ch))
            {
                if (current.Length > 0)
                {
                    terms.Add(current.ToString());
                    current.Clear();
                }
            }
            else
            {
                current.Append(ch);
            }
        }

        if (current.Length > 0)
        {
            terms.Add(current.ToString());
        }

        return terms;
    }

    private static bool MatchesTerms(string searchText, IReadOnlyList<string> terms, string mode)
    {
        return mode == "or"
            ? terms.Any(term => searchText.Contains(term, StringComparison.Ordinal))
            : terms.All(term => searchText.Contains(term, StringComparison.Ordinal));
    }

    private static int AppendSearchChunk(List<string> chunks, string? text, int currentLength, int limit)
    {
        var normalized = NormalizeSearchText(text);
        if (string.IsNullOrWhiteSpace(normalized) || currentLength >= limit)
        {
            return currentLength;
        }

        var remaining = limit - currentLength;
        if (normalized.Length > remaining)
        {
            normalized = normalized[..remaining];
        }

        chunks.Add(normalized);
        return currentLength + normalized.Length;
    }

    private static SessionSummaryDto UpdateMinMaxEventTimestamps(SessionSummaryDto summary, string timestamp)
    {
        if (string.IsNullOrWhiteSpace(timestamp))
        {
            return summary;
        }

        var min = string.IsNullOrWhiteSpace(summary.MinEventTs) || string.CompareOrdinal(timestamp, summary.MinEventTs) < 0
            ? timestamp
            : summary.MinEventTs;
        var max = string.IsNullOrWhiteSpace(summary.MaxEventTs) || string.CompareOrdinal(timestamp, summary.MaxEventTs) > 0
            ? timestamp
            : summary.MaxEventTs;
        return summary with { MinEventTs = min, MaxEventTs = max };
    }

    private static bool HasEventLabel(LabelStoreSnapshot snapshot, string path, int labelId)
    {
        return snapshot.EventLabels.TryGetValue(path, out var eventMap)
            && eventMap.Values.Any(ids => ids.Contains(labelId));
    }

    private static IEnumerable<LabeledEventListItemDto> BuildLabeledEventItems(
        SessionSummaryDto session,
        IReadOnlyList<SessionEventDto> events,
        IReadOnlyDictionary<string, IReadOnlyList<int>> labelsByEventId,
        IReadOnlyDictionary<int, LabelDto> labelById)
    {
        if (labelsByEventId.Count == 0 || events.Count == 0)
        {
            yield break;
        }

        foreach (var @event in events)
        {
            if (string.IsNullOrWhiteSpace(@event.EventId) || !labelsByEventId.TryGetValue(@event.EventId, out var labelIds))
            {
                continue;
            }

            var labels = ResolveLabels(labelIds, labelById);
            if (labels.Count == 0)
            {
                continue;
            }

            yield return ToLabeledEventItem(session, @event, labels);
        }
    }

    private static IReadOnlyList<LabelDto> ResolveLabels(IEnumerable<int> ids, IReadOnlyDictionary<int, LabelDto> labelById)
    {
        return ids
            .Distinct()
            .Select(id => labelById.TryGetValue(id, out var label) ? label : null)
            .Where(label => label is not null)
            .Cast<LabelDto>()
            .OrderBy(label => label.Name, StringComparer.OrdinalIgnoreCase)
            .ThenBy(label => label.Id)
            .ToArray();
    }

    private static string GetSessionSortKey(SessionSummaryDto session)
    {
        return !string.IsNullOrWhiteSpace(session.StartedAt) ? session.StartedAt : session.Mtime;
    }

    private static SessionSummaryDto WithSessionLabels(SessionSummaryDto session, IReadOnlyList<int> labelIds, IReadOnlyList<LabelDto> labels)
    {
        return session with { SessionLabelIds = labelIds, SessionLabels = labels };
    }

    private static SessionSummaryDto WithSessionLabelIds(SessionSummaryDto session, IReadOnlyList<int> labelIds)
    {
        return session with { SessionLabelIds = labelIds };
    }

    private static LabeledEventListItemDto ToLabeledEventItem(
        SessionSummaryDto session,
        SessionEventDto @event,
        IReadOnlyList<LabelDto> labels)
    {
        return new LabeledEventListItemDto
        {
            Path = session.Path,
            RelativePath = session.RelativePath,
            SessionId = session.SessionId,
            SessionStartedAt = session.StartedAt,
            SessionMtime = session.Mtime,
            Cwd = session.Cwd,
            Source = !string.IsNullOrWhiteSpace(session.SourceType) ? session.SourceType : session.Source,
            EventId = @event.EventId,
            Timestamp = @event.Timestamp,
            Kind = @event.Kind,
            Role = @event.Role,
            Preview = BuildLabeledEventPreview(@event),
            Labels = labels,
        };
    }

    private static SessionEventDto WithEventLabels(SessionEventDto @event, IReadOnlyList<LabelDto> labels)
    {
        return new SessionEventDto
        {
            EventId = @event.EventId,
            Timestamp = @event.Timestamp,
            Kind = @event.Kind,
            Role = @event.Role,
            Text = @event.Text,
            Model = @event.Model,
            EffortLevel = @event.EffortLevel,
            Name = @event.Name,
            Arguments = @event.Arguments,
            CallId = @event.CallId,
            Output = @event.Output,
            SystemLabels = @event.SystemLabels,
            Usage = @event.Usage,
            Labels = labels,
        };
    }

    private static string BuildLabeledEventPreview(SessionEventDto @event)
    {
        var text = @event.Kind switch
        {
            "message" => @event.Text,
            "function_call" => string.Join(' ', new[] { @event.Name, @event.Arguments }.Where(value => !string.IsNullOrWhiteSpace(value))),
            "function_output" => @event.Output,
            "agent_update" => @event.Text,
            "token_usage" => BuildTokenUsagePreview(@event.Usage),
            _ => string.Join(' ', new[] { @event.Text, @event.Output, @event.Arguments }.Where(value => !string.IsNullOrWhiteSpace(value))),
        };

        return string.IsNullOrWhiteSpace(text)
            ? string.Empty
            : CollapseNewlines(text, 220);
    }

    private static string BuildTokenUsagePreview(UsageMetricsDto? usage)
    {
        if (usage is null)
        {
            return string.Empty;
        }

        var parts = new List<string>
        {
            $"total {usage.TotalTokens.ToString("N0", CultureInfo.InvariantCulture)}"
        };

        if (usage.CacheCreationTokens > 0)
        {
            parts.Add($"cache write {usage.CacheCreationTokens.ToString("N0", CultureInfo.InvariantCulture)}");
        }

        if (usage.CacheReadTokens > 0)
        {
            parts.Add($"cache read {usage.CacheReadTokens.ToString("N0", CultureInfo.InvariantCulture)}");
        }

        if (usage.CostUsd.HasValue)
        {
            parts.Add($"cost ${usage.CostUsd.Value.ToString("0.####", CultureInfo.InvariantCulture)}");
        }

        return string.Join(" / ", parts);
    }

    private static string BuildRootSummaryText(RootsDto roots)
    {
        var parts = new List<string>();
        if (roots.ClaudeCli.Count > 0)
        {
            parts.AddRange(roots.ClaudeCli.Select(root => $"Claude Code CLI: {root}"));
        }

        if (roots.ClaudeDesktop.Count > 0)
        {
            parts.AddRange(roots.ClaudeDesktop.Select(root => $"Claude Desktop: {root}"));
        }

        return string.Join(" | ", parts);
    }

    private static string SafeRelativePath(string path, string root)
    {
        try
        {
            return Path.GetRelativePath(root, path);
        }
        catch
        {
            return path;
        }
    }

    private static IReadOnlyList<string> ExistingOrUnique(IEnumerable<string> paths)
    {
        var unique = paths
            .Where(path => !string.IsNullOrWhiteSpace(path))
            .Distinct(PathComparer)
            .ToArray();
        var existing = unique.Where(path => File.Exists(path) || Directory.Exists(path)).ToArray();
        return existing.Length > 0 ? existing : unique;
    }

    private static bool IsWithinRoot(string candidate, string root)
    {
        if (string.Equals(candidate, root, PathComparison))
        {
            return true;
        }

        var normalizedRoot = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            + Path.DirectorySeparatorChar;
        return candidate.StartsWith(normalizedRoot, PathComparison);
    }

    private static IEnumerable<string> SafeEnumerateFiles(string root, string pattern, EnumerationOptions options)
    {
        try
        {
            return Directory.EnumerateFiles(root, pattern, options).ToArray();
        }
        catch
        {
            return [];
        }
    }

    private static IEnumerable<string> SafeEnumerateDirectories(string root)
    {
        try
        {
            return Directory.EnumerateDirectories(root).ToArray();
        }
        catch
        {
            return [];
        }
    }

    private static DateTime SafeGetLastWriteTimeUtc(string path)
    {
        try
        {
            return File.GetLastWriteTimeUtc(path);
        }
        catch
        {
            return DateTime.MinValue;
        }
    }

    private static FileSignature GetSignature(FileInfo fileInfo)
    {
        return new FileSignature(fileInfo.LastWriteTimeUtc.Ticks, fileInfo.Length);
    }

    private static string BuildSessionVersion(FileInfo fileInfo)
    {
        var signature = GetSignature(fileInfo);
        return $"{signature.LastWriteTicks}:{signature.Size}";
    }

    private static string CanonicalizePath(string rawPath)
    {
        foreach (var candidate in ExpandPathCandidates(rawPath))
        {
            if (File.Exists(candidate) || Directory.Exists(candidate))
            {
                return Path.GetFullPath(candidate);
            }
        }

        var first = ExpandPathCandidates(rawPath).FirstOrDefault();
        return Path.GetFullPath(first ?? rawPath);
    }

    private static string SessionPathKey(string path)
    {
        return CanonicalizePath(path);
    }

    private static IEnumerable<string> ExpandPathCandidates(string rawPath)
    {
        var candidate = Environment.ExpandEnvironmentVariables(rawPath ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(candidate))
        {
            return [];
        }

        if (candidate.StartsWith('~'))
        {
            candidate = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                candidate[1..].TrimStart('/', '\\'));
        }

        if (OperatingSystem.IsWindows())
        {
            if (WindowsPathRegex.IsMatch(candidate) || candidate.StartsWith(@"\\", StringComparison.Ordinal))
            {
                return [Path.GetFullPath(candidate)];
            }

            var fromMount = WslMountPathToWindows(candidate);
            if (!string.IsNullOrWhiteSpace(fromMount))
            {
                return [Path.GetFullPath(fromMount)];
            }

            return [Path.GetFullPath(candidate)];
        }

        var converted = WindowsPathToWsl(candidate);
        if (!string.IsNullOrWhiteSpace(converted) && !File.Exists(candidate) && !Directory.Exists(candidate))
        {
            candidate = converted;
        }

        return [Path.GetFullPath(candidate)];
    }

    private static string? WslMountPathToWindows(string rawPath)
    {
        var normalized = rawPath.Replace('\\', '/');
        var match = WslMountPathRegex.Match(normalized);
        if (!match.Success)
        {
            return null;
        }

        var drive = match.Groups[1].Value.ToUpperInvariant();
        var rest = match.Groups[2].Success
            ? match.Groups[2].Value.Replace('/', Path.DirectorySeparatorChar)
            : string.Empty;
        return string.IsNullOrEmpty(rest)
            ? $"{drive}:{Path.DirectorySeparatorChar}"
            : $"{drive}:{Path.DirectorySeparatorChar}{rest}";
    }

    private static string? WindowsPathToWsl(string rawPath)
    {
        var match = WindowsPathRegex.Match(rawPath);
        if (!match.Success)
        {
            return null;
        }

        var drive = match.Groups[1].Value.ToLowerInvariant();
        var rest = match.Groups[2].Value.Replace('\\', '/').TrimStart('/');
        return $"/mnt/{drive}/{rest}";
    }

    private static string ToIsoLocal(DateTime value)
    {
        return value.ToString("s");
    }

    private static string CollapseNewlines(string text, int maxLength)
    {
        var collapsed = NormalizeInlineText(text.Replace('\n', ' ').Replace('\r', ' '));
        return collapsed.Length > maxLength ? collapsed[..maxLength] : collapsed;
    }

    private static string FirstNonEmpty(params string[] values)
    {
        return values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? string.Empty;
    }

    private static IReadOnlyList<CostSummaryGroupDefinition> BuildCostSummaryGroupDefinitions(DateTime nowLocal)
    {
        var today = nowLocal.Date;
        var thisMonthStart = new DateTime(today.Year, today.Month, 1, 0, 0, 0, DateTimeKind.Unspecified);
        var thisWeekStart = StartOfWeek(today, DayOfWeek.Monday);

        return
        [
            new CostSummaryGroupDefinition(
                "month",
                [
                    new CostSummaryPeriodDefinition("two_months_ago", thisMonthStart.AddMonths(-2), thisMonthStart.AddMonths(-1)),
                    new CostSummaryPeriodDefinition("last_month", thisMonthStart.AddMonths(-1), thisMonthStart),
                    new CostSummaryPeriodDefinition("this_month", thisMonthStart, thisMonthStart.AddMonths(1)),
                ]),
            new CostSummaryGroupDefinition(
                "week",
                [
                    new CostSummaryPeriodDefinition("two_weeks_ago", thisWeekStart.AddDays(-14), thisWeekStart.AddDays(-7)),
                    new CostSummaryPeriodDefinition("last_week", thisWeekStart.AddDays(-7), thisWeekStart),
                    new CostSummaryPeriodDefinition("this_week", thisWeekStart, thisWeekStart.AddDays(7)),
                ]),
            new CostSummaryGroupDefinition(
                "day",
                [
                    new CostSummaryPeriodDefinition("two_days_ago", today.AddDays(-2), today.AddDays(-1)),
                    new CostSummaryPeriodDefinition("yesterday", today.AddDays(-1), today),
                    new CostSummaryPeriodDefinition("today", today, today.AddDays(1)),
                ]),
        ];
    }

    private static DateTime StartOfWeek(DateTime value, DayOfWeek firstDayOfWeek)
    {
        var diff = (7 + (value.DayOfWeek - firstDayOfWeek)) % 7;
        return value.Date.AddDays(-diff);
    }

    private static bool TryParseLocalTimestamp(string timestamp, out DateTime localTimestamp)
    {
        if (string.IsNullOrWhiteSpace(timestamp))
        {
            localTimestamp = default;
            return false;
        }

        if (DateTimeOffset.TryParse(
            timestamp,
            CultureInfo.InvariantCulture,
            DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeLocal,
            out var dto))
        {
            localTimestamp = dto.ToLocalTime().DateTime;
            return true;
        }

        localTimestamp = default;
        return false;
    }

    private static string JoinWithNewline(params string[] values)
    {
        return string.Join("\n", values.Where(value => !string.IsNullOrWhiteSpace(value))).Trim();
    }

    private static void AddDistinctModel(List<string> models, string modelName)
    {
        if (string.IsNullOrWhiteSpace(modelName))
        {
            return;
        }

        if (models.Any(existing => string.Equals(existing, modelName, StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }

        models.Add(modelName);
    }

    private static UsageMetricsDto CreateUsageMetrics(
        long inputTokens,
        long outputTokens,
        long cacheCreationTokens,
        long cacheReadTokens,
        decimal? costUsd,
        decimal? inputCostUsd = null,
        decimal? cacheCreationCostUsd = null,
        decimal? cacheReadCostUsd = null,
        decimal? outputCostUsd = null)
    {
        return new UsageMetricsDto
        {
            InputTokens = inputTokens,
            OutputTokens = outputTokens,
            CacheCreationTokens = cacheCreationTokens,
            CacheReadTokens = cacheReadTokens,
            TotalTokens = inputTokens + outputTokens + cacheCreationTokens + cacheReadTokens,
            InputCostUsd = inputCostUsd,
            CacheCreationCostUsd = cacheCreationCostUsd,
            CacheReadCostUsd = cacheReadCostUsd,
            OutputCostUsd = outputCostUsd,
            CostUsd = costUsd,
        };
    }

    private static decimal? TryExtractCostUsd(JsonElement eventElement, JsonElement usageElement)
    {
        if (GetDecimal(eventElement, "costUSD") is decimal topLevelCost)
        {
            return topLevelCost;
        }

        if (TryGetProperty(eventElement, "cost", out var costObject)
            && costObject.ValueKind == JsonValueKind.Object)
        {
            if (GetDecimal(costObject, "total_cost_usd") is decimal totalCostUsd)
            {
                return totalCostUsd;
            }

            if (GetDecimal(costObject, "totalCostUsd") is decimal totalCostUsdCamel)
            {
                return totalCostUsdCamel;
            }
        }

        if (GetDecimal(usageElement, "cost_usd") is decimal usageCostUsd)
        {
            return usageCostUsd;
        }

        return GetDecimal(usageElement, "costUSD");
    }

    private static string GetString(JsonElement element, string propertyName)
    {
        return TryGetProperty(element, propertyName, out var value)
            ? GetElementText(value)
            : string.Empty;
    }

    private static string TryGetNestedString(JsonElement element, params string[] path)
    {
        var current = element;
        foreach (var segment in path)
        {
            if (current.ValueKind != JsonValueKind.Object)
            {
                return string.Empty;
            }

            if (!TryGetProperty(current, segment, out current))
            {
                return string.Empty;
            }
        }

        return GetElementText(current).Trim();
    }

    private static long GetInt64(JsonElement element, string propertyName)
    {
        if (!TryGetProperty(element, propertyName, out var value))
        {
            return 0;
        }

        switch (value.ValueKind)
        {
            case JsonValueKind.Number:
                return value.TryGetInt64(out var whole)
                    ? whole
                    : (long)Math.Round(value.GetDouble(), MidpointRounding.AwayFromZero);

            case JsonValueKind.String:
            {
                var text = value.GetString()?.Trim() ?? string.Empty;
                return long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed)
                    ? parsed
                    : 0;
            }

            default:
                return 0;
        }
    }

    private static decimal? GetDecimal(JsonElement element, string propertyName)
    {
        if (!TryGetProperty(element, propertyName, out var value))
        {
            return null;
        }

        switch (value.ValueKind)
        {
            case JsonValueKind.Number:
                return value.TryGetDecimal(out var number)
                    ? number
                    : (decimal)value.GetDouble();

            case JsonValueKind.String:
            {
                var text = value.GetString()?.Trim() ?? string.Empty;
                return decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed)
                    ? parsed
                    : null;
            }

            default:
                return null;
        }
    }

    private static bool TryGetProperty(JsonElement element, string propertyName, out JsonElement value)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            value = default;
            return false;
        }

        foreach (var property in element.EnumerateObject())
        {
            if (string.Equals(property.Name, propertyName, StringComparison.Ordinal))
            {
                value = property.Value;
                return true;
            }
        }

        value = default;
        return false;
    }

    private static string GetElementText(JsonElement element)
    {
        return element.ValueKind switch
        {
            JsonValueKind.String => element.GetString() ?? string.Empty,
            JsonValueKind.Number => element.ToString(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Object or JsonValueKind.Array => SerializeElement(element),
            _ => string.Empty,
        };
    }

    private static string SerializeElement(JsonElement element, bool indented = false)
    {
        try
        {
            return JsonSerializer.Serialize(element, indented ? PrettyJsonOptions : (JsonSerializerOptions?)null);
        }
        catch
        {
            return element.ToString();
        }
    }

    private static int CountItems(JsonElement element, string propertyName)
    {
        return TryGetProperty(element, propertyName, out var value) && value.ValueKind == JsonValueKind.Array
            ? value.GetArrayLength()
            : 0;
    }

    private static byte[] ReadRawBytes(string path, int limit)
    {
        using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        var length = (int)Math.Min(Math.Max(limit, 0), stream.Length);
        var buffer = new byte[length];
        var offset = 0;
        while (offset < buffer.Length)
        {
            var read = stream.Read(buffer, offset, buffer.Length - offset);
            if (read <= 0)
            {
                break;
            }

            offset += read;
        }

        return offset == buffer.Length ? buffer : buffer[..offset];
    }

    private static bool TryParseJson(string text, out JsonDocument document)
    {
        try
        {
            document = JsonDocument.Parse(text);
            return true;
        }
        catch
        {
            document = null!;
            return false;
        }
    }

    private static string ToWslUncPath(string distro, string posixPath)
    {
        if (string.IsNullOrWhiteSpace(distro) || string.IsNullOrWhiteSpace(posixPath) || !posixPath.StartsWith("/", StringComparison.Ordinal))
        {
            return string.Empty;
        }

        var suffix = posixPath.Trim('/').Replace('/', '\\');
        var basePath = $@"\\wsl.localhost\{distro}";
        return string.IsNullOrWhiteSpace(suffix) ? basePath : $@"{basePath}\{suffix}";
    }

    private static string ProjectDisplayLabel(string rawProject, string cwd, string root)
    {
        var wslDistro = ExtractWslDistroFromRoot(root);
        if (!string.IsNullOrWhiteSpace(cwd))
        {
            return ToWindowsPathDisplay(cwd, wslDistro);
        }

        var label = DecodeProjectSlugToWindowsPath(rawProject);
        if (!string.IsNullOrWhiteSpace(wslDistro)
            && !string.IsNullOrWhiteSpace(label)
            && !Regex.IsMatch(label, @"^(?:[A-Za-z]:\\|\\\\)", RegexOptions.CultureInvariant))
        {
            return $@"\\wsl.localhost\{wslDistro}\{label.TrimStart('\\')}";
        }

        return label;
    }

    private static string ExtractWslDistroFromRoot(string root)
    {
        var match = WslUncRootRegex.Match(root ?? string.Empty);
        return match.Success ? match.Groups[1].Value : string.Empty;
    }

    private static string ToWindowsPathDisplay(string path, string wslDistro)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return string.Empty;
        }

        var normalized = path.Trim();
        var mountMatch = WslMountPathRegex.Match(normalized.Replace('\\', '/'));
        if (mountMatch.Success)
        {
            var drive = mountMatch.Groups[1].Value.ToUpperInvariant();
            var rest = mountMatch.Groups[2].Success ? mountMatch.Groups[2].Value.Replace('/', '\\') : string.Empty;
            return string.IsNullOrWhiteSpace(rest) ? $"{drive}:\\" : $@"{drive}:\{rest}";
        }

        if (normalized.StartsWith("/", StringComparison.Ordinal))
        {
            if (!string.IsNullOrWhiteSpace(wslDistro))
            {
                var rest = normalized.Trim('/').Replace('/', '\\');
                var basePath = $@"\\wsl.localhost\{wslDistro}";
                return string.IsNullOrWhiteSpace(rest) ? basePath : $@"{basePath}\{rest}";
            }

            return normalized;
        }

        var converted = normalized.Replace('/', '\\');
        var slugMatch = Regex.Match(converted, @"^([A-Za-z]:)\\-([^\\]+)$", RegexOptions.CultureInvariant);
        if (slugMatch.Success)
        {
            var drive = slugMatch.Groups[1].Value.ToUpperInvariant();
            var tail = string.Join("\\", slugMatch.Groups[2].Value.Split('-', StringSplitOptions.RemoveEmptyEntries));
            return string.IsNullOrWhiteSpace(tail) ? $"{drive}\\" : $@"{drive}\{tail}";
        }

        return converted;
    }

    private static string DecodeProjectSlugToWindowsPath(string projectSlug)
    {
        if (string.IsNullOrWhiteSpace(projectSlug))
        {
            return string.Empty;
        }

        var normalized = projectSlug.Trim();
        if (normalized.Contains('/') || normalized.Contains('\\') || !normalized.Contains('-'))
        {
            return normalized;
        }

        var parts = normalized.TrimStart('-')
            .Split('-', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (parts.Length == 0)
        {
            return normalized;
        }

        if (parts.Length >= 3
            && string.Equals(parts[0], "mnt", StringComparison.OrdinalIgnoreCase)
            && parts[1].Length == 1
            && char.IsLetter(parts[1][0]))
        {
            var drive = char.ToUpperInvariant(parts[1][0]);
            var tail = string.Join("\\", parts.Skip(2));
            return string.IsNullOrWhiteSpace(tail) ? $"{drive}:\\" : $@"{drive}:\{tail}";
        }

        if (parts.Length >= 2 && parts[0].Length == 1 && char.IsLetter(parts[0][0]))
        {
            var drive = char.ToUpperInvariant(parts[0][0]);
            var tail = string.Join("\\", parts.Skip(1));
            return string.IsNullOrWhiteSpace(tail) ? $"{drive}:\\" : $@"{drive}:\{tail}";
        }

        return string.Join("\\", parts);
    }

    private readonly record struct SessionItem(string SourceType, string Path, string Root);

    private readonly record struct FileSignature(long LastWriteTicks, long Size);

    private sealed record CostSummaryCacheEntry(
        DateTimeOffset BuiltAtUtc,
        long PricingVersion,
        CostSummaryResponse Response);

    private sealed record SessionItemsCacheEntry(
        string RootsKey,
        DateTime BuiltAtUtc,
        IReadOnlyList<SessionItem> Items);

    private sealed class SessionCacheEntry
    {
        public FileSignature Signature { get; init; }

        public IndexRecord? IndexRecord { get; init; }

        public EventsData? EventsData { get; init; }

        public long ViewerSettingsVersion { get; init; }

        public int MaxEvents { get; init; }

        private long _lastAccessedTicks = Environment.TickCount64;

        public long LastAccessedTicks
        {
            get => Volatile.Read(ref _lastAccessedTicks);
            set => Volatile.Write(ref _lastAccessedTicks, value);
        }
    }

    private sealed class EventsData
    {
        public EventsData(IReadOnlyList<SessionEventDto> events, int rawLineCount)
        {
            Events = events;
            RawLineCount = rawLineCount;
        }

        public IReadOnlyList<SessionEventDto> Events { get; }

        public int RawLineCount { get; }
    }

    private sealed class IndexRecord
    {
        public IndexRecord(SessionSummaryDto summary, string searchText)
        {
            Summary = summary;
            SearchText = searchText;
        }

        public SessionSummaryDto Summary { get; }

        public string SearchText { get; }
    }

    private sealed class UsageAccumulator
    {
        private long _inputTokens;
        private long _outputTokens;
        private long _cacheCreationTokens;
        private long _cacheReadTokens;
        private decimal _inputCostUsd;
        private decimal _cacheCreationCostUsd;
        private decimal _cacheReadCostUsd;
        private decimal _outputCostUsd;
        private decimal _costUsd;
        private bool _hasUsage;
        private bool _hasCompleteCost = true;
        private bool _hasCompleteCostBreakdown = true;

        public void Add(UsageMetricsDto? usage)
        {
            if (usage is null)
            {
                return;
            }

            _hasUsage = true;
            _inputTokens += usage.InputTokens;
            _outputTokens += usage.OutputTokens;
            _cacheCreationTokens += usage.CacheCreationTokens;
            _cacheReadTokens += usage.CacheReadTokens;

            if (usage.InputCostUsd.HasValue
                && usage.CacheCreationCostUsd.HasValue
                && usage.CacheReadCostUsd.HasValue
                && usage.OutputCostUsd.HasValue)
            {
                _inputCostUsd += usage.InputCostUsd.Value;
                _cacheCreationCostUsd += usage.CacheCreationCostUsd.Value;
                _cacheReadCostUsd += usage.CacheReadCostUsd.Value;
                _outputCostUsd += usage.OutputCostUsd.Value;
            }
            else
            {
                _hasCompleteCostBreakdown = false;
            }

            if (usage.CostUsd.HasValue)
            {
                _costUsd += usage.CostUsd.Value;
            }
            else
            {
                _hasCompleteCost = false;
            }
        }

        public UsageMetricsDto? ToUsage()
        {
            return !_hasUsage
                ? null
                : CreateUsageMetrics(
                    _inputTokens,
                    _outputTokens,
                    _cacheCreationTokens,
                    _cacheReadTokens,
                    _hasCompleteCost ? _costUsd : null,
                    _hasCompleteCostBreakdown ? _inputCostUsd : null,
                    _hasCompleteCostBreakdown ? _cacheCreationCostUsd : null,
                    _hasCompleteCostBreakdown ? _cacheReadCostUsd : null,
                    _hasCompleteCostBreakdown ? _outputCostUsd : null);
        }
    }

    private sealed record CostSummaryPeriodDefinition(
        string Key,
        DateTime StartLocal,
        DateTime EndLocal);

    private sealed record CostSummaryGroupDefinition(
        string Key,
        IReadOnlyList<CostSummaryPeriodDefinition> Periods);

    private sealed class CostSummaryGroupAccumulator
    {
        private readonly CostSummaryGroupDefinition _definition;
        private readonly CostSummaryBucketAccumulator[] _sessions;
        private readonly CostSummaryBucketAccumulator[] _tokenUsageEvents;

        public CostSummaryGroupAccumulator(CostSummaryGroupDefinition definition)
        {
            _definition = definition;
            _sessions = definition.Periods.Select(_ => new CostSummaryBucketAccumulator()).ToArray();
            _tokenUsageEvents = definition.Periods.Select(_ => new CostSummaryBucketAccumulator()).ToArray();
        }

        public void AddSessionUsage(DateTime localTimestamp, UsageMetricsDto usage)
        {
            if (TryGetPeriodIndex(localTimestamp, out var index))
            {
                _sessions[index].Add(usage);
            }
        }

        public void AddTokenUsageEvent(DateTime localTimestamp, UsageMetricsDto usage)
        {
            if (TryGetPeriodIndex(localTimestamp, out var index))
            {
                _tokenUsageEvents[index].Add(usage);
            }
        }

        public CostSummaryGroupDto ToDto()
        {
            return new CostSummaryGroupDto
            {
                Key = _definition.Key,
                Sessions = _definition.Periods
                    .Select((period, index) => _sessions[index].ToDto(period.Key))
                    .ToArray(),
                TokenUsageEvents = _definition.Periods
                    .Select((period, index) => _tokenUsageEvents[index].ToDto(period.Key))
                    .ToArray(),
            };
        }

        private bool TryGetPeriodIndex(DateTime localTimestamp, out int index)
        {
            for (var i = 0; i < _definition.Periods.Count; i++)
            {
                var period = _definition.Periods[i];
                if (localTimestamp >= period.StartLocal && localTimestamp < period.EndLocal)
                {
                    index = i;
                    return true;
                }
            }

            index = -1;
            return false;
        }
    }

    private sealed class CostSummaryBucketAccumulator
    {
        private int _itemCount;
        private long _inputTokens;
        private long _cacheCreationTokens;
        private long _cacheReadTokens;
        private long _outputTokens;
        private long _totalTokens;
        private decimal _inputCostUsd;
        private decimal _cacheCreationCostUsd;
        private decimal _cacheReadCostUsd;
        private decimal _outputCostUsd;
        private decimal _costUsd;
        private bool _hasUnknownCost;
        private bool _hasUnknownCostBreakdown;

        public void Add(UsageMetricsDto usage)
        {
            _itemCount++;
            _inputTokens += usage.InputTokens;
            _cacheCreationTokens += usage.CacheCreationTokens;
            _cacheReadTokens += usage.CacheReadTokens;
            _outputTokens += usage.OutputTokens;
            _totalTokens += usage.TotalTokens;

            if (usage.InputCostUsd.HasValue
                && usage.CacheCreationCostUsd.HasValue
                && usage.CacheReadCostUsd.HasValue
                && usage.OutputCostUsd.HasValue)
            {
                _inputCostUsd += usage.InputCostUsd.Value;
                _cacheCreationCostUsd += usage.CacheCreationCostUsd.Value;
                _cacheReadCostUsd += usage.CacheReadCostUsd.Value;
                _outputCostUsd += usage.OutputCostUsd.Value;
            }
            else
            {
                _hasUnknownCostBreakdown = true;
            }

            if (usage.CostUsd.HasValue)
            {
                _costUsd += usage.CostUsd.Value;
            }
            else
            {
                _hasUnknownCost = true;
            }
        }

        public CostSummaryPeriodDto ToDto(string key)
        {
            var cacheTokens = _cacheCreationTokens + _cacheReadTokens;
            return new CostSummaryPeriodDto
            {
                Key = key,
                ItemCount = _itemCount,
                InputTokens = _inputTokens,
                CacheCreationTokens = _cacheCreationTokens,
                CacheReadTokens = _cacheReadTokens,
                CacheTokens = cacheTokens,
                OutputTokens = _outputTokens,
                TotalTokens = _totalTokens,
                InputCostUsd = _hasUnknownCostBreakdown ? null : _inputCostUsd,
                CacheCreationCostUsd = _hasUnknownCostBreakdown ? null : _cacheCreationCostUsd,
                CacheReadCostUsd = _hasUnknownCostBreakdown ? null : _cacheReadCostUsd,
                OutputCostUsd = _hasUnknownCostBreakdown ? null : _outputCostUsd,
                CostUsd = _hasUnknownCost ? null : _costUsd,
            };
        }
    }

    private readonly record struct LevelDbEntry(byte[] Key, byte[] Value);

    private readonly record struct DesktopEntry(string IdbKey, string Text, string UpdatedAt, int AttachCount);
}
