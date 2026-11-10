using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace LifeLog.Core;

/// <summary>
/// <see cref="Db"/>의 API 모드 구현(세컨뇌 DB 이전 7단계). 같은 일을 SecondBrainApi /api/lifelog/*로 한다.
/// 기기는 서버가 API 키(종류 collector, 이름 = 기기 id)에서 정하므로 여기서 보내는 device는 쓰이지 않는다.
/// 서버에 닿지 않으면 HttpRequestException·TaskCanceledException이 나고, 업로더는 기존과 같이 큐에 남겨 다시 보낸다.
/// </summary>
internal sealed class DbApi : IDisposable
{
    private readonly HttpClient _http;
    private static readonly JsonSerializerOptions Web = new(JsonSerializerDefaults.Web);

    public DbApi(string baseUrl, string key)
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/"), Timeout = TimeSpan.FromSeconds(30) };
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key.Trim());
        _http.DefaultRequestHeaders.UserAgent.ParseAdd("secondbrain-collector/api");
    }

    static string E(string s) => Uri.EscapeDataString(s);

    async Task<JsonElement?> Send(HttpMethod method, string path, string? json, CancellationToken ct, bool notFoundIsNull = false)
    {
        using var req = new HttpRequestMessage(method, "api/lifelog/" + path);
        if (json is not null) req.Content = new StringContent(json, Encoding.UTF8, "application/json");
        HttpResponseMessage resMsg;
        try { resMsg = await _http.SendAsync(req, ct); }
        catch (TaskCanceledException e) when (!ct.IsCancellationRequested)
        {
            // HttpClient 시간 초과도 TaskCanceledException이다. 업로더는 OperationCanceledException을 "종료"로 보므로 재시도할 오류로 바꾼다
            throw new HttpRequestException("API 응답 시간 초과", e);
        }
        using var res = resMsg;
        if (notFoundIsNull && res.StatusCode == HttpStatusCode.NotFound) return null;
        var text = await res.Content.ReadAsStringAsync(ct);
        if (!res.IsSuccessStatusCode)
        {
            string reason = $"{(int)res.StatusCode}";
            try { if (JsonDocument.Parse(text).RootElement.TryGetProperty("detail", out var d)) reason += " " + d.GetString(); } catch (JsonException) { }
            // 4xx는 다시 보내도 같은 결과라 InvalidOperationException(업로더가 그 행을 버리지 않고 알린다), 5xx·연결 실패는 재시도 대상
            if ((int)res.StatusCode is >= 400 and < 500) throw new InvalidOperationException("API 거절: " + reason);
            throw new HttpRequestException("API 오류: " + reason);
        }
        return text.Length == 0 ? null : JsonDocument.Parse(text).RootElement.Clone();
    }

    public async Task<int> SchemaVersion(CancellationToken ct) => (await Send(HttpMethod.Get, "schema", null, ct))!.Value.GetProperty("version").GetInt32();

    public async Task<DateTimeOffset> ServerNow(CancellationToken ct) => (await Send(HttpMethod.Get, "now", null, ct))!.Value.GetProperty("now").GetDateTimeOffset();

    public async Task<JsonElement> RegisterPlugin(string manifestJson, CancellationToken ct) => (await Send(HttpMethod.Post, "plugins", manifestJson, ct))!.Value;

    public async Task<Db.Install?> GetInstall(string plugin, CancellationToken ct)
    {
        var j = await Send(HttpMethod.Get, $"installs/{E(plugin)}", null, ct, notFoundIsNull: true);
        if (j is not { } v) return null;
        return new Db.Install(v.GetProperty("installedVersion").GetString()!, v.GetProperty("enabled").GetBoolean(),
            v.GetProperty("settings").Clone(), v.GetProperty("granted").EnumerateArray().Select(e => e.GetString()!).ToHashSet());
    }

    public async Task<JsonElement> SetInstall(string plugin, string version, bool enabled, string? settingsJson, string grantedJson, CancellationToken ct)
    {
        var body = $"{{\"version\":{JsonSerializer.Serialize(version)},\"enabled\":{(enabled ? "true" : "false")},\"settings\":{settingsJson ?? "null"},\"granted\":{grantedJson}}}";
        return (await Send(HttpMethod.Put, $"installs/{E(plugin)}", body, ct))!.Value;
    }

    public async Task<long> CountToday(string view, CancellationToken ct) =>
        (await Send(HttpMethod.Get, $"count?view={E(view)}", null, ct))!.Value.GetProperty("count").GetInt64();

    public async Task<string> Query(QuerySpec q, CancellationToken ct)
    {
        var opts = new Dictionary<string, object?>
        {
            ["range"] = new { start = q.Start, end = q.End },
            ["tz"] = q.Tz,
            ["limit"] = q.Limit,
        };
        if (q.Device is not null) opts["device"] = q.Device;
        if (q.Where.Count > 0) opts["where"] = q.Where;
        if (q.GroupTime is not null || q.GroupBy.Count > 0)
        {
            var g = new Dictionary<string, object>();
            if (q.GroupTime is not null) g["time"] = q.GroupTime;
            if (q.GroupBy.Count > 0) g["by"] = q.GroupBy;
            opts["group"] = g;
        }
        if (q.Agg.Count > 0) opts["agg"] = q.Agg;
        var res = await Send(HttpMethod.Post, $"query?table={E(q.Table)}", JsonSerializer.Serialize(opts, Web), ct);
        return res!.Value.GetRawText();
    }

    public async Task<List<TableMeta>> CollectorTables(string plugin, CancellationToken ct) =>
        (await Send(HttpMethod.Get, $"plugins/{E(plugin)}/tables", null, ct))!.Value.EnumerateArray()
            .Select(t => new TableMeta(t.GetProperty("table").GetString()!, t.GetProperty("isSpan").GetBoolean(),
                t.GetProperty("columns").EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetString()!)))
            .ToList();

    public async Task<List<Db.OutputInfo>> ListOutputs(CancellationToken ct) =>
        (await Send(HttpMethod.Get, "outputs", null, ct))!.Value.EnumerateArray()
            .Select(o => new Db.OutputInfo(o.GetProperty("plugin").GetString()!, o.GetProperty("output").GetString()!, o.GetProperty("schemaVersion").GetInt32(),
                o.GetProperty("isSpan").GetBoolean(), o.GetProperty("columns").EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetString()!),
                o.GetProperty("label").GetString() ?? "", o.GetProperty("pluginName").GetString() ?? ""))
            .ToList();

    public Task SetPluginSource(string plugin, string url, CancellationToken ct) =>
        Send(HttpMethod.Put, $"plugins/{E(plugin)}/source", JsonSerializer.Serialize(new { url }), ct);

    public async Task<JsonElement> Registry(CancellationToken ct) => (await Send(HttpMethod.Get, "registry", null, ct))!.Value;

    public async Task<JsonElement?> CategoryRules(CancellationToken ct) => await Send(HttpMethod.Get, "category-rules", null, ct);

    public Task SetCategoryRules(string kind, string rulesJson, CancellationToken ct) => Send(HttpMethod.Put, $"category-rules/{E(kind)}", rulesJson, ct);

    public Task Touch(CancellationToken ct) => Send(HttpMethod.Post, "touch", null, ct);

    public Task<JsonElement?> StateGet(string plugin, string key, CancellationToken ct) =>
        Send(HttpMethod.Get, $"state/{E(plugin)}/{E(key)}", null, ct, notFoundIsNull: true);

    public Task StateSet(string plugin, string key, string valueJson, CancellationToken ct) =>
        Send(HttpMethod.Put, $"state/{E(plugin)}/{E(key)}", valueJson, ct);

    public Task Upsert(TableMeta meta, IReadOnlyList<Row> rows, CancellationToken ct)
    {
        var body = JsonSerializer.Serialize(new
        {
            rows = rows.Select(r => new { id = r.Id, start = r.Start, end = meta.IsSpan ? r.End ?? r.Start : (DateTimeOffset?)null, cols = r.Cols }),
        }, Web);
        return Send(HttpMethod.Post, $"rows?table={E(meta.Table)}", body, ct);
    }

    public Task Delete(string table, Guid id, CancellationToken ct) => Send(HttpMethod.Delete, $"rows?table={E(table)}&id={id}", null, ct);

    public void Dispose() => _http.Dispose();
}
