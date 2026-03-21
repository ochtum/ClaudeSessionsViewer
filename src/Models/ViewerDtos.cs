namespace ClaudeSessionsViewer.Models;

public sealed class LabelDto
{
    public int Id { get; init; }

    public string Name { get; init; } = string.Empty;

    public string ColorValue { get; init; } = string.Empty;

    public string ColorFamily { get; init; } = string.Empty;

    public string ColorFamilyLabel { get; init; } = string.Empty;
}

public sealed record SessionSummaryDto
{
    public string Id { get; init; } = string.Empty;

    public string Path { get; init; } = string.Empty;

    public string RelativePath { get; init; } = string.Empty;

    public string Mtime { get; init; } = string.Empty;

    public string SessionId { get; init; } = string.Empty;

    public string StartedAt { get; init; } = string.Empty;

    public string Cwd { get; init; } = string.Empty;

    public string Model { get; init; } = string.Empty;

    public string Source { get; init; } = string.Empty;

    public string SourceType { get; init; } = string.Empty;

    public string Project { get; init; } = string.Empty;

    public string FirstUserText { get; init; } = string.Empty;

    public string FirstRealUserText { get; init; } = string.Empty;

    public string MinEventTs { get; init; } = string.Empty;

    public string MaxEventTs { get; init; } = string.Empty;

    public IReadOnlyList<LabelDto> SessionLabels { get; init; } = [];
}

public sealed class SessionEventDto
{
    public string EventId { get; init; } = string.Empty;

    public string Timestamp { get; init; } = string.Empty;

    public string Kind { get; init; } = string.Empty;

    public string Role { get; init; } = string.Empty;

    public string Text { get; init; } = string.Empty;

    public string Name { get; init; } = string.Empty;

    public string Arguments { get; init; } = string.Empty;

    public string CallId { get; init; } = string.Empty;

    public string Output { get; init; } = string.Empty;

    public IReadOnlyList<string> SystemLabels { get; init; } = [];

    public IReadOnlyList<LabelDto> Labels { get; init; } = [];
}

public sealed class RootsDto
{
    public IReadOnlyList<string> ClaudeCli { get; init; } = [];

    public IReadOnlyList<string> ClaudeDesktop { get; init; } = [];
}

public sealed class LabelsResponse
{
    public IReadOnlyList<LabelDto> Labels { get; init; } = [];
}

public sealed class SaveLabelResponse
{
    public LabelDto? Label { get; init; }
}

public sealed class SessionListResponse
{
    public string Root { get; init; } = string.Empty;

    public RootsDto Roots { get; init; } = new();

    public IReadOnlyList<SessionSummaryDto> Sessions { get; init; } = [];
}

public sealed class SessionDetailResponse
{
    public SessionSummaryDto? Session { get; init; }

    public IReadOnlyList<SessionEventDto> Events { get; init; } = [];

    public int RawLineCount { get; init; }
}

public sealed class OkResponse
{
    public bool Ok { get; init; }
}

public sealed class SaveLabelRequest
{
    public int? Id { get; init; }

    public string? Name { get; init; }

    public string? ColorValue { get; init; }

    public string? ColorFamily { get; init; }
}

public sealed class DeleteLabelRequest
{
    public int? Id { get; init; }
}

public sealed class SessionLabelMutationRequest
{
    public string? Path { get; init; }

    public int? LabelId { get; init; }
}

public sealed class EventLabelMutationRequest
{
    public string? Path { get; init; }

    public string? EventId { get; init; }

    public int? LabelId { get; init; }
}
