using System.Diagnostics;
using System.IO.Compression;
using System.Net;
using System.Net.NetworkInformation;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using ClaudeSessionsViewer.Components;
using ClaudeSessionsViewer.Models;
using ClaudeSessionsViewer.Services;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.ResponseCompression;

namespace ClaudeSessionsViewer;

public class Program
{
    private const string ViewerSectionName = "Viewer";
    private const string DefaultViewerUrl = "http://127.0.0.1:8767";
    private static Mutex? SingleInstanceMutex;

    public static void Main(string[] args)
    {
        TrySetConsoleTitle();

        var (contentRootPath, webRootPath) = ResolveAppPaths();
        if (!TryAcquireSingleInstanceLock(contentRootPath))
        {
            return;
        }

        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            Args = args,
            ContentRootPath = contentRootPath,
            WebRootPath = webRootPath,
        });
        var configuredViewerDefaultUrl = builder.Configuration.GetValue<string>($"{ViewerSectionName}:DefaultUrl") ?? DefaultViewerUrl;
        var viewerDefaultUrl = ResolveAvailableDefaultUrl(builder.Configuration, configuredViewerDefaultUrl);
        var launchBrowserOnStartup = builder.Configuration.GetValue<bool?>($"{ViewerSectionName}:LaunchBrowserOnStartup")
            ?? !builder.Environment.IsDevelopment();

        ConfigureDefaultUrl(builder, viewerDefaultUrl);

        // Add services to the container.
        builder.Services.AddRazorComponents()
            .AddInteractiveServerComponents();
        builder.Services.ConfigureHttpJsonOptions(options =>
        {
            options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
            options.SerializerOptions.DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower;
            options.SerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
        });
        builder.Services.AddResponseCompression(options =>
        {
            options.EnableForHttps = true;
            options.Providers.Add<BrotliCompressionProvider>();
            options.Providers.Add<GzipCompressionProvider>();
            options.MimeTypes = ResponseCompressionDefaults.MimeTypes.Concat(
            [
                "application/json",
                "text/javascript",
                "application/javascript",
            ]);
        });
        builder.Services.Configure<BrotliCompressionProviderOptions>(options =>
        {
            options.Level = CompressionLevel.Fastest;
        });
        builder.Services.Configure<GzipCompressionProviderOptions>(options =>
        {
            options.Level = CompressionLevel.Fastest;
        });
        builder.Services.AddWebOptimizer(pipeline =>
        {
            pipeline.MinifyCssFiles("css/**/*.css");
        });
        builder.Services.AddHttpClient();
        builder.Services.AddHttpClient("exchange-rates", client =>
        {
            client.Timeout = TimeSpan.FromSeconds(5);
        });
        builder.Services.AddSingleton<LabelStore>();
        builder.Services.AddSingleton<ViewerSettingsStore>();
        builder.Services.AddSingleton<ExchangeRateService>();
        builder.Services.AddSingleton<ViewerService>();

        var app = builder.Build();
        LogResolvedUrl(app, configuredViewerDefaultUrl, viewerDefaultUrl);
        ConfigureBrowserLaunch(app, viewerDefaultUrl, launchBrowserOnStartup);

        // Configure the HTTP request pipeline.
        if (!app.Environment.IsDevelopment())
        {
            app.UseExceptionHandler("/Error");
        }

        app.UseStatusCodePagesWithReExecute("/not-found", createScopeForStatusCodePages: true);
        app.UseAntiforgery();
        app.UseResponseCompression();
        app.UseWebOptimizer();

        app.UseStaticFiles(new StaticFileOptions
        {
            OnPrepareResponse = context =>
            {
                var headers = context.Context.Response.Headers;
                headers.CacheControl = "public, max-age=86400";
            },
        });
        MapApi(app);
        app.MapRazorComponents<App>()
            .AddInteractiveServerRenderMode();

        app.Run();
    }

    private static void TrySetConsoleTitle()
    {
        try
        {
            Console.Title = "ClaudeSessionsViewer";
        }
        catch (IOException)
        {
        }
        catch (PlatformNotSupportedException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static bool TryAcquireSingleInstanceLock(string contentRootPath)
    {
        try
        {
            var mutex = new Mutex(false, BuildSingleInstanceMutexName("ClaudeSessionsViewer", contentRootPath));
            var hasHandle = false;
            try
            {
                hasHandle = mutex.WaitOne(0, false);
            }
            catch (AbandonedMutexException)
            {
                hasHandle = true;
            }

            if (!hasHandle)
            {
                mutex.Dispose();
                Console.Error.WriteLine("ClaudeSessionsViewer is already running for this app folder.");
                return false;
            }

            SingleInstanceMutex = mutex;
            AppDomain.CurrentDomain.ProcessExit += (_, _) => ReleaseSingleInstanceLock();
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return true;
        }
        catch (IOException)
        {
            return true;
        }
    }

    private static void ReleaseSingleInstanceLock()
    {
        try
        {
            SingleInstanceMutex?.ReleaseMutex();
        }
        catch (ApplicationException)
        {
        }
        finally
        {
            SingleInstanceMutex?.Dispose();
            SingleInstanceMutex = null;
        }
    }

    private static string BuildSingleInstanceMutexName(string appName, string contentRootPath)
    {
        var normalizedRoot = Path.GetFullPath(contentRootPath)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .ToUpperInvariant();
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(normalizedRoot)));
        return $"{appName}_{hash}";
    }

    private static void MapApi(WebApplication app)
    {
        app.MapGet("/api/labels", async (ViewerService viewer, CancellationToken cancellationToken) =>
        {
            return Results.Ok(await viewer.GetLabelsAsync(cancellationToken));
        });

        app.MapGet("/api/labeled-items", async (ViewerService viewer, CancellationToken cancellationToken) =>
        {
            return Results.Ok(await viewer.GetLabeledItemsAsync(cancellationToken));
        });

        app.MapGet("/api/cost-summary", async (ViewerService viewer, CancellationToken cancellationToken) =>
        {
            return Results.Ok(await viewer.GetCostSummaryAsync(cancellationToken));
        });

        app.MapGet("/api/sessions", async (HttpRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            var query = request.Query;
            var response = await viewer.GetSessionsAsync(
                query["q"],
                query["mode"],
                query["sort"],
                ParseOptionalInt(query["session_label_id"]),
                ParseOptionalInt(query["event_label_id"]),
                cancellationToken);
            return Results.Ok(response);
        });

        app.MapGet("/api/session", async (HttpRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            try
            {
                var includeEvents = !string.Equals(request.Query["include_events"], "false", StringComparison.OrdinalIgnoreCase);
                var response = await viewer.GetSessionAsync(request.Query["path"], request.Query["source"], includeEvents, cancellationToken);
                return Results.Ok(response);
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status400BadRequest);
            }
            catch (FileNotFoundException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status404NotFound);
            }
            catch (IOException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status500InternalServerError);
            }
            catch (UnauthorizedAccessException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status500InternalServerError);
            }
        });

        app.MapPost("/api/labels/save", async (SaveLabelRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            try
            {
                var label = await viewer.SaveLabelAsync(request, cancellationToken);
                return Results.Ok(new SaveLabelResponse { Label = label });
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status400BadRequest);
            }
        });

        app.MapPost("/api/labels/delete", async (DeleteLabelRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            if (request.Id is null)
            {
                return Results.Json(new { error = "label id is required" }, statusCode: StatusCodes.Status400BadRequest);
            }

            await viewer.DeleteLabelAsync(request.Id.Value, cancellationToken);
            return Results.Ok(new OkResponse { Ok = true });
        });

        app.MapPost("/api/session-label/add", async (SessionLabelMutationRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            try
            {
                await viewer.AddSessionLabelAsync(request, cancellationToken);
                return Results.Ok(new OkResponse { Ok = true });
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status400BadRequest);
            }
        });

        app.MapPost("/api/session-label/remove", async (SessionLabelMutationRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            try
            {
                await viewer.RemoveSessionLabelAsync(request, cancellationToken);
                return Results.Ok(new OkResponse { Ok = true });
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status400BadRequest);
            }
        });

        app.MapPost("/api/event-label/add", async (EventLabelMutationRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            try
            {
                await viewer.AddEventLabelAsync(request, cancellationToken);
                return Results.Ok(new OkResponse { Ok = true });
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status400BadRequest);
            }
        });

        app.MapPost("/api/event-label/remove", async (EventLabelMutationRequest request, ViewerService viewer, CancellationToken cancellationToken) =>
        {
            try
            {
                await viewer.RemoveEventLabelAsync(request, cancellationToken);
                return Results.Ok(new OkResponse { Ok = true });
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message }, statusCode: StatusCodes.Status400BadRequest);
            }
        });
    }

    private static int? ParseOptionalInt(string? raw)
    {
        return int.TryParse(raw, out var value) ? value : null;
    }

    private static void ConfigureDefaultUrl(WebApplicationBuilder builder, string defaultUrl)
    {
        if (string.IsNullOrWhiteSpace(defaultUrl))
        {
            return;
        }

        if (!string.IsNullOrWhiteSpace(builder.WebHost.GetSetting(WebHostDefaults.ServerUrlsKey)))
        {
            return;
        }

        if (builder.Configuration.GetSection("Kestrel:Endpoints").Exists())
        {
            return;
        }

        builder.WebHost.UseUrls(defaultUrl);
    }

    private static void ConfigureBrowserLaunch(WebApplication app, string fallbackUrl, bool launchBrowserOnStartup)
    {
        if (!launchBrowserOnStartup)
        {
            return;
        }

        app.Lifetime.ApplicationStarted.Register(() =>
        {
            _ = Task.Run(() =>
            {
                var launchUrl = ResolveLaunchUrl(app, fallbackUrl);

                try
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = launchUrl,
                        UseShellExecute = true
                    });
                }
                catch (Exception ex)
                {
                    app.Logger.LogWarning(ex, "Failed to launch browser for {Url}", launchUrl);
                }
            });
        });
    }

    private static string ResolveLaunchUrl(WebApplication app, string fallbackUrl)
    {
        var urls = app.Urls
            .Where(url => Uri.TryCreate(url, UriKind.Absolute, out _))
            .Select(url => new Uri(url))
            .ToList();

        if (urls.Count == 0)
        {
            return fallbackUrl;
        }

        if (Uri.TryCreate(fallbackUrl, UriKind.Absolute, out var fallbackUri))
        {
            var exactMatch = urls.FirstOrDefault(url =>
                string.Equals(url.Scheme, fallbackUri.Scheme, StringComparison.OrdinalIgnoreCase) &&
                string.Equals(url.Host, fallbackUri.Host, StringComparison.OrdinalIgnoreCase) &&
                url.Port == fallbackUri.Port);

            if (exactMatch is not null)
            {
                return exactMatch.ToString();
            }
        }

        var preferredHttpLoopback = urls.FirstOrDefault(url =>
            string.Equals(url.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
            (string.Equals(url.Host, "127.0.0.1", StringComparison.OrdinalIgnoreCase) ||
             string.Equals(url.Host, "localhost", StringComparison.OrdinalIgnoreCase)));

        if (preferredHttpLoopback is not null)
        {
            return preferredHttpLoopback.ToString();
        }

        var preferredHttp = urls.FirstOrDefault(url =>
            string.Equals(url.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase));

        return preferredHttp?.ToString() ?? urls[0].ToString();
    }

    private static string ResolveAvailableDefaultUrl(IConfiguration configuration, string configuredDefaultUrl)
    {
        var autoSelectAvailablePort = configuration.GetValue<bool?>($"{ViewerSectionName}:AutoSelectAvailablePortOnConflict") ?? true;

        if (!autoSelectAvailablePort)
        {
            return configuredDefaultUrl;
        }

        if (!Uri.TryCreate(configuredDefaultUrl, UriKind.Absolute, out var configuredUri))
        {
            return configuredDefaultUrl;
        }

        if (!string.Equals(configuredUri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
            !string.Equals(configuredUri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
        {
            return configuredDefaultUrl;
        }

        if (configuredUri.Port <= 0)
        {
            return configuredDefaultUrl;
        }

        var fallbackRange = Math.Max(configuration.GetValue<int?>($"{ViewerSectionName}:PortFallbackRange") ?? 20, 0);
        var activePorts = IPGlobalProperties.GetIPGlobalProperties()
            .GetActiveTcpListeners()
            .Select(endpoint => endpoint.Port)
            .ToHashSet();

        if (!activePorts.Contains(configuredUri.Port))
        {
            return configuredDefaultUrl;
        }

        for (var port = configuredUri.Port + 1; port <= Math.Min(IPEndPoint.MaxPort, configuredUri.Port + fallbackRange); port++)
        {
            if (activePorts.Contains(port))
            {
                continue;
            }

            var uriBuilder = new UriBuilder(configuredUri)
            {
                Port = port
            };

            return uriBuilder.Uri.ToString();
        }

        return configuredDefaultUrl;
    }

    private static void LogResolvedUrl(WebApplication app, string configuredDefaultUrl, string resolvedDefaultUrl)
    {
        if (string.Equals(configuredDefaultUrl, resolvedDefaultUrl, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        app.Logger.LogWarning(
            "The configured default URL {ConfiguredDefaultUrl} is already in use. Falling back to {ResolvedDefaultUrl}.",
            configuredDefaultUrl,
            resolvedDefaultUrl);
    }


    private static (string ContentRootPath, string? WebRootPath) ResolveAppPaths()
    {
        var payloadRoot = Path.Combine(AppContext.BaseDirectory, "payload");
        if (LooksLikeContentRoot(payloadRoot))
        {
            return (payloadRoot, ResolveWebRootPath(payloadRoot));
        }

        var currentDirectoryRoot = FindNearestContentRoot(Directory.GetCurrentDirectory());
        if (currentDirectoryRoot is not null)
        {
            return (currentDirectoryRoot, ResolveWebRootPath(currentDirectoryRoot));
        }

        var baseDirectoryRoot = FindNearestContentRoot(AppContext.BaseDirectory);
        if (baseDirectoryRoot is not null)
        {
            return (baseDirectoryRoot, ResolveWebRootPath(baseDirectoryRoot));
        }

        var currentDirectory = Directory.GetCurrentDirectory();
        return (currentDirectory, ResolveWebRootPath(currentDirectory));
    }

    private static bool LooksLikeContentRoot(string path)
    {
        return File.Exists(Path.Combine(path, "appsettings.json")) ||
               Directory.Exists(Path.Combine(path, "wwwroot"));
    }

    private static string? FindNearestContentRoot(string startPath)
    {
        if (string.IsNullOrWhiteSpace(startPath))
        {
            return null;
        }

        var directory = new DirectoryInfo(Path.GetFullPath(startPath));
        while (directory is not null)
        {
            if (LooksLikeContentRoot(directory.FullName))
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        return null;
    }

    private static string? ResolveWebRootPath(string contentRootPath)
    {
        var webRootPath = Path.Combine(contentRootPath, "wwwroot");
        return Directory.Exists(webRootPath) ? webRootPath : null;
    }
}
