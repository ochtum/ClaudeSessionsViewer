namespace ClaudeSessionsViewer.Services;

internal static class AppStoragePathHelper
{
    public static string ResolveCacheRoot(string contentRootPath)
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
}
