using System.Text.Json;
using Npgsql;
using NpgsqlTypes;

namespace LifeLog.Core;

/// <summary>DB 접근. 등록 함수 호출, 스키마 버전 확인, 시계 보정, 행 upsert. 기기 롤로 붙는다.</summary>
public sealed class Db : IAsyncDisposable
{
    /// <summary>이 앱이 아는 코어 스키마 버전. DB가 더 높으면 쓰기를 멈춘다(Documents/등록 함수.md 4절).</summary>
    public const int KnownSchemaVersion = 7;

    /// <summary>수집기가 돌 수 있는 가장 낮은 코어 스키마. 0007(공유 규칙·platforms)은 없어도 수집은 된다.</summary>
    public const int MinSchemaVersion = 6;

    /// <summary>0007부터: 공유 분류 규칙 테이블, 매니페스트 platforms·requires.</summary>
    public const int RulesSchemaVersion = 7;
    private int? _schema;

    private readonly NpgsqlDataSource _ds = null!;
    /// <summary>API 모드(세컨뇌 DB 이전 7단계)면 DB 대신 SecondBrainApi를 부른다. null이면 기존처럼 DB에 직접 붙는다.</summary>
    private readonly DbApi? _api;
    public TimeSpan ClockOffset { get; private set; }   // DB now() - 로컬 now

    public Db(string connectionString)
    {
        var b = new NpgsqlDataSourceBuilder(connectionString);
        _ds = b.Build();
    }

    private Db(DbApi api) => _api = api;

    /// <summary>설정에 API 주소가 있으면 API 모드, 아니면 기기 롤로 DB에 직접.</summary>
    public static Db Open(Config cfg) => cfg.UsesApi ? new Db(new DbApi(cfg.ApiUrl, cfg.GetApiKey())) : new Db(cfg.ConnectionString());

    public bool UsesApi => _api is not null;

    public async Task<int> SchemaVersionAsync(CancellationToken ct = default)
    {
        if (_api is not null) { var av = await _api.SchemaVersion(ct); _schema = av; return av; }
        await using var cmd = _ds.CreateCommand("SELECT COALESCE(max(version), 0) FROM lifelog.schema_migrations");
        var v = Convert.ToInt32(await cmd.ExecuteScalarAsync(ct));
        _schema = v;
        return v;
    }

    /// <summary>처음 한 번만 DB에 묻고 기억한다.</summary>
    public async Task<int> CachedSchemaAsync(CancellationToken ct = default) => _schema ?? await SchemaVersionAsync(ct);

    public async Task SyncClockAsync(CancellationToken ct = default)
    {
        if (_api is not null) { var b0 = DateTimeOffset.UtcNow; var sn = await _api.ServerNow(ct); var a0 = DateTimeOffset.UtcNow; ClockOffset = sn - (b0 + (a0 - b0) / 2); return; }
        await using var cmd = _ds.CreateCommand("SELECT now()");
        var before = DateTimeOffset.UtcNow;
        var dbNow = (DateTime)(await cmd.ExecuteScalarAsync(ct))!;
        var after = DateTimeOffset.UtcNow;
        var mid = before + (after - before) / 2;
        ClockOffset = new DateTimeOffset(dbNow, TimeSpan.Zero) - mid;
    }

    public DateTimeOffset Now() => DateTimeOffset.UtcNow + ClockOffset;

    public async Task<JsonElement> RegisterPluginAsync(Manifest m, string device, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.RegisterPlugin(m.Raw, ct);
        // 0006 DB는 platforms·requires를 모른다. 두 키를 빼고 등록하고, 0007이 적용되면 같은 버전으로 다시 등록할 때 채워진다
        var raw = m.Raw;
        if (await CachedSchemaAsync(ct) < RulesSchemaVersion)
        {
            var node = System.Text.Json.Nodes.JsonNode.Parse(raw)!.AsObject();
            node.Remove("platforms"); node.Remove("requires");
            raw = node.ToJsonString();
        }
        await using var cmd = _ds.CreateCommand("SELECT lifelog.register_plugin($1::jsonb, $2)");
        cmd.Parameters.Add(new NpgsqlParameter { Value = raw, NpgsqlDbType = NpgsqlDbType.Jsonb });
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        var s = (string)(await cmd.ExecuteScalarAsync(ct))!;
        return JsonDocument.Parse(s).RootElement.Clone();
    }

    public sealed record Install(string InstalledVersion, bool Enabled, JsonElement Settings, HashSet<string> Granted);

    public async Task<Install?> GetInstallAsync(string plugin, string device, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.GetInstall(plugin, ct);
        await using var cmd = _ds.CreateCommand("SELECT installed_version, enabled, settings::text, granted::text FROM lifelog.plugin_installs WHERE plugin_id = $1 AND device = $2");
        cmd.Parameters.Add(new NpgsqlParameter { Value = plugin });
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        await using var r = await cmd.ExecuteReaderAsync(ct);
        if (!await r.ReadAsync(ct)) return null;
        var granted = JsonDocument.Parse(r.GetString(3)).RootElement.EnumerateArray().Select(e => e.GetString()!).ToHashSet();
        return new Install(r.GetString(0), r.GetBoolean(1), JsonDocument.Parse(r.GetString(2)).RootElement.Clone(), granted);
    }

    public async Task<JsonElement> SetInstallAsync(string plugin, string device, string version, bool enabled, string? settingsJson, string grantedJson, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.SetInstall(plugin, version, enabled, settingsJson, grantedJson, ct);
        await using var cmd = _ds.CreateCommand("SELECT lifelog.set_install($1, $2, $3, $4, $5::jsonb, $6::jsonb)");
        cmd.Parameters.Add(new NpgsqlParameter { Value = plugin });
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        cmd.Parameters.Add(new NpgsqlParameter { Value = version });
        cmd.Parameters.Add(new NpgsqlParameter { Value = enabled });
        cmd.Parameters.Add(new NpgsqlParameter { Value = (object?)settingsJson ?? DBNull.Value, NpgsqlDbType = NpgsqlDbType.Jsonb });
        cmd.Parameters.Add(new NpgsqlParameter { Value = grantedJson, NpgsqlDbType = NpgsqlDbType.Jsonb });
        var s = (string)(await cmd.ExecuteScalarAsync(ct))!;
        return JsonDocument.Parse(s).RootElement.Clone();
    }

    /// <summary>뷰에서 이 기기의 오늘(UTC 기준 24시간) 행 수. status 명령용.</summary>
    public async Task<long> CountTodayAsync(string view, bool isSpan, string device, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.CountToday(view, ct);
        var col = isSpan ? "start" : "time";
        await using var cmd = _ds.CreateCommand($"SELECT count(*) FROM lifelog.\"{view.Replace("\"", "\"\"")}\" WHERE device = $1 AND {col} > now() - interval '24 hours'");
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        return Convert.ToInt64(await cmd.ExecuteScalarAsync(ct));
    }

    /// <summary>렌더러 query 실행. 결과는 행 배열 JSON. 상한을 넘으면 truncated=true.</summary>
    public async Task<string> QueryAsync(QuerySpec q, TableMeta meta, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.Query(q, ct);
        var (sql, ps) = QueryBuilder.Build(q, meta);
        await using var cmd = _ds.CreateCommand(sql);
        foreach (var p in ps) cmd.Parameters.Add(p);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        var rows = new List<Dictionary<string, object?>>();
        while (await r.ReadAsync(ct))
        {
            var row = new Dictionary<string, object?>();
            for (int i = 0; i < r.FieldCount; i++)
            {
                var v = r.IsDBNull(i) ? null : r.GetValue(i);
                row[r.GetName(i)] = v switch
                {
                    DateTime dt => new DateTimeOffset(DateTime.SpecifyKind(dt, DateTimeKind.Utc)).ToString("O"),
                    Guid g => g.ToString(),
                    _ => v,
                };
            }
            rows.Add(row);
        }
        bool truncated = rows.Count > q.Limit;
        if (truncated) rows.RemoveAt(rows.Count - 1);
        return JsonSerializer.Serialize(new { rows, truncated });
    }

    /// <summary>등록부에서 수집기 출력 목록(테이블 메타 + 매니페스트의 라벨 열·kind). 내장 타임라인용.</summary>
    /// <summary>플러그인의 모든 출력 테이블(옛 schema_version 포함). 출력을 올린 뒤 큐에 남은 옛 버전 행을 올릴 때 쓴다.</summary>
    public async Task<List<TableMeta>> CollectorTablesAsync(string plugin, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.CollectorTables(plugin, ct);
        await using var cmd = _ds.CreateCommand("SELECT table_name, kind, columns::text FROM lifelog.collector_tables WHERE plugin_id = $1");
        cmd.Parameters.Add(new NpgsqlParameter { Value = plugin });
        await using var r = await cmd.ExecuteReaderAsync(ct);
        var list = new List<TableMeta>();
        while (await r.ReadAsync(ct))
            list.Add(new TableMeta(r.GetString(0), r.GetString(1) == "span", JsonSerializer.Deserialize<Dictionary<string, string>>(r.GetString(2))!));
        return list;
    }

    public async Task<List<OutputInfo>> ListOutputsAsync(CancellationToken ct = default)
    {
        if (_api is not null) return await _api.ListOutputs(ct);
        await using var cmd = _ds.CreateCommand(
            "SELECT ct.plugin_id, ct.output, ct.schema_version, ct.kind, ct.columns::text, pv.manifest::text " +
            "FROM lifelog.collector_tables ct JOIN lifelog.plugins p ON p.id = ct.plugin_id " +
            "JOIN lifelog.plugin_versions pv ON pv.plugin_id = p.id AND pv.version = p.latest_version ORDER BY ct.plugin_id, ct.output, ct.schema_version");
        await using var r = await cmd.ExecuteReaderAsync(ct);
        var list = new List<OutputInfo>();
        while (await r.ReadAsync(ct))
        {
            var cols = JsonSerializer.Deserialize<Dictionary<string, string>>(r.GetString(4))!;
            var manifest = JsonDocument.Parse(r.GetString(5)).RootElement;
            string label = "", name = r.GetString(0);
            if (manifest.TryGetProperty("name", out var n)) name = n.GetString() ?? name;
            foreach (var o in manifest.GetProperty("outputs").EnumerateArray())
                if (o.GetProperty("name").GetString() == r.GetString(1)) label = o.GetProperty("label").GetString() ?? "";
            list.Add(new OutputInfo(r.GetString(0), r.GetString(1), r.GetInt32(2), r.GetString(3) == "span", cols, label, name));
        }
        return list;
    }

    public sealed record OutputInfo(string Plugin, string Output, int SchemaVersion, bool IsSpan, Dictionary<string, string> Columns, string Label, string PluginName)
    {
        public string Table => $"{Plugin}/{Output}@{SchemaVersion}";
        public string View => $"{Plugin}/{Output}";
    }

    /// <summary>plugins.source_url(0006). URL로 설치했을 때 적는다.</summary>
    public async Task SetPluginSourceAsync(string plugin, string url, CancellationToken ct = default)
    {
        if (_api is not null) { await _api.SetPluginSource(plugin, url, ct); return; }
        await using var cmd = _ds.CreateCommand("SELECT lifelog.set_plugin_source($1, $2)");
        cmd.Parameters.Add(new NpgsqlParameter { Value = plugin });
        cmd.Parameters.Add(new NpgsqlParameter { Value = url });
        await cmd.ExecuteNonQueryAsync(ct);
    }

    /// <summary>등록부 전체: 기기, 플러그인(최신 버전·이름·출처·기기별 설치·테이블). 대시보드 플러그인 페이지용.</summary>
    public async Task<JsonElement> ListRegistryAsync(CancellationToken ct = default)
    {
        if (_api is not null) return await _api.Registry(ct);
        const string sql = @"SELECT json_build_object(
  'devices', (SELECT COALESCE(json_agg(json_build_object('id', id, 'name', name, 'platform', platform, 'last_seen', last_seen, 'revoked', revoked_at IS NOT NULL) ORDER BY id), '[]'::json) FROM lifelog.devices),
  'plugins', (SELECT COALESCE(json_agg(json_build_object(
      'id', p.id, 'type', p.type, 'latest_version', p.latest_version, 'source_url', p.source_url,
      'name', (SELECT v.manifest->>'name' FROM lifelog.plugin_versions v WHERE v.plugin_id = p.id AND v.version = p.latest_version),
      'platforms', (SELECT v.manifest->'platforms' FROM lifelog.plugin_versions v WHERE v.plugin_id = p.id AND v.version = p.latest_version),
      'requires', (SELECT v.manifest->'requires' FROM lifelog.plugin_versions v WHERE v.plugin_id = p.id AND v.version = p.latest_version),
      'installs', (SELECT COALESCE(json_agg(json_build_object('device', i.device, 'version', i.installed_version, 'enabled', i.enabled, 'granted', i.granted, 'unmet', i.unmet, 'last_seen', i.last_seen) ORDER BY i.device), '[]'::json) FROM lifelog.plugin_installs i WHERE i.plugin_id = p.id),
      'tables', (SELECT COALESCE(json_agg(t.table_name ORDER BY t.table_name), '[]'::json) FROM lifelog.collector_tables t WHERE t.plugin_id = p.id)
    ) ORDER BY p.id), '[]'::json) FROM lifelog.plugins p))::text";
        await using var cmd = _ds.CreateCommand(sql);
        var json = (string)(await cmd.ExecuteScalarAsync(ct))!;
        return JsonDocument.Parse(json).RootElement.Clone();
    }

    /// <summary>공유 분류 규칙(0007). { pc: [...], phone: [...] }. 0006 DB면 null(호출자가 로컬 파일을 쓴다).</summary>
    public async Task<JsonElement?> GetCategoryRulesAsync(CancellationToken ct = default)
    {
        if (_api is not null) return await _api.CategoryRules(ct);
        if (await CachedSchemaAsync(ct) < RulesSchemaVersion) return null;
        await using var cmd = _ds.CreateCommand(
            "SELECT json_build_object('pc', (SELECT rules FROM lifelog.category_rules WHERE kind = 'pc'), " +
            "'phone', (SELECT rules FROM lifelog.category_rules WHERE kind = 'phone'))::text");
        var json = (string)(await cmd.ExecuteScalarAsync(ct))!;
        return JsonDocument.Parse(json).RootElement.Clone();
    }

    /// <summary>규칙 목록 전체를 바꾼다. kind: pc | phone.</summary>
    public async Task SetCategoryRulesAsync(string kind, string rulesJson, CancellationToken ct = default)
    {
        if (_api is not null) { await _api.SetCategoryRules(kind, rulesJson, ct); return; }
        await using var cmd = _ds.CreateCommand("SELECT lifelog.set_category_rules($1, $2::jsonb)");
        cmd.Parameters.Add(new NpgsqlParameter { Value = kind });
        cmd.Parameters.Add(new NpgsqlParameter { Value = rulesJson, NpgsqlDbType = NpgsqlDbType.Jsonb });
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task TouchLastSeenAsync(string device, CancellationToken ct = default)
    {
        if (_api is not null) { await _api.Touch(ct); return; }
        await using var cmd = _ds.CreateCommand("UPDATE lifelog.devices SET last_seen = now() WHERE id = $1");
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<JsonElement?> StateGetAsync(string plugin, string device, string key, CancellationToken ct = default)
    {
        if (_api is not null) return await _api.StateGet(plugin, key, ct);
        await using var cmd = _ds.CreateCommand("SELECT value::text FROM lifelog.plugin_state WHERE plugin_id = $1 AND device = $2 AND key = $3");
        cmd.Parameters.Add(new NpgsqlParameter { Value = plugin });
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        cmd.Parameters.Add(new NpgsqlParameter { Value = key });
        var s = await cmd.ExecuteScalarAsync(ct) as string;
        return s is null ? null : JsonDocument.Parse(s).RootElement.Clone();
    }

    public async Task StateSetAsync(string plugin, string device, string key, string valueJson, CancellationToken ct = default)
    {
        if (_api is not null) { await _api.StateSet(plugin, key, valueJson, ct); return; }
        await using var cmd = _ds.CreateCommand(
            "INSERT INTO lifelog.plugin_state (plugin_id, device, key, value) VALUES ($1, $2, $3, $4::jsonb) " +
            "ON CONFLICT (plugin_id, device, key) DO UPDATE SET value = EXCLUDED.value");
        cmd.Parameters.Add(new NpgsqlParameter { Value = plugin });
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        cmd.Parameters.Add(new NpgsqlParameter { Value = key });
        cmd.Parameters.Add(new NpgsqlParameter { Value = valueJson, NpgsqlDbType = NpgsqlDbType.Jsonb });
        await cmd.ExecuteNonQueryAsync(ct);
    }

    /// <summary>한 테이블의 행 여러 개를 (device, id) upsert. 한 트랜잭션.</summary>
    public async Task UpsertAsync(TableMeta meta, IReadOnlyList<Row> rows, CancellationToken ct = default)
    {
        if (_api is not null) { if (rows.Count > 0) await _api.Upsert(meta, rows, ct); return; }
        if (rows.Count == 0) return;
        var cols = meta.Columns.Keys.OrderBy(k => k, StringComparer.Ordinal).ToList();
        var timeCols = meta.IsSpan ? "start, \"end\"" : "time";
        var colList = string.Join(", ", cols.Select(Q));
        var updates = string.Join(", ", (meta.IsSpan ? new[] { "\"end\" = EXCLUDED.\"end\"" } : Array.Empty<string>())
            .Concat(cols.Select(c => $"{Q(c)} = EXCLUDED.{Q(c)}")));
        var n = 2 + (meta.IsSpan ? 2 : 1) + cols.Count;
        var placeholders = string.Join(", ", Enumerable.Range(1, n).Select(i => "$" + i));
        var sql = $"INSERT INTO lifelog.{Q(meta.Table)} (device, id, {timeCols}{(cols.Count > 0 ? ", " + colList : "")}) VALUES ({placeholders}) " +
                  $"ON CONFLICT (device, id) DO UPDATE SET {updates}";

        await using var conn = await _ds.OpenConnectionAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        await using var batch = new NpgsqlBatch(conn, tx);
        foreach (var r in rows)
        {
            var cmd = new NpgsqlBatchCommand(sql);
            cmd.Parameters.Add(new NpgsqlParameter { Value = r.Device });
            cmd.Parameters.Add(new NpgsqlParameter { Value = r.Id, NpgsqlDbType = NpgsqlDbType.Uuid });
            cmd.Parameters.Add(new NpgsqlParameter { Value = r.Start.UtcDateTime, NpgsqlDbType = NpgsqlDbType.TimestampTz });
            if (meta.IsSpan)
                cmd.Parameters.Add(new NpgsqlParameter { Value = (r.End ?? r.Start).UtcDateTime, NpgsqlDbType = NpgsqlDbType.TimestampTz });
            foreach (var c in cols)
                cmd.Parameters.Add(Param(meta.Columns[c], r.Cols.TryGetValue(c, out var v) ? v : default));
            batch.BatchCommands.Add(cmd);
        }
        await batch.ExecuteNonQueryAsync(ct);
        await tx.CommitAsync(ct);
    }

    public async Task DeleteAsync(string table, string device, Guid id, CancellationToken ct = default)
    {
        if (_api is not null) { await _api.Delete(table, id, ct); return; }
        await using var cmd = _ds.CreateCommand($"DELETE FROM lifelog.{Q(table)} WHERE device = $1 AND id = $2");
        cmd.Parameters.Add(new NpgsqlParameter { Value = device });
        cmd.Parameters.Add(new NpgsqlParameter { Value = id, NpgsqlDbType = NpgsqlDbType.Uuid });
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private static string Q(string ident) => "\"" + ident.Replace("\"", "\"\"") + "\"";

    /// <summary>매니페스트 열 타입에 맞춰 JSON 값을 파라미터로. null/undefined는 NULL.</summary>
    private static NpgsqlParameter Param(string type, JsonElement v)
    {
        bool isNull = v.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null;
        return type switch
        {
            "text" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Text, Value = isNull ? DBNull.Value : (v.ValueKind == JsonValueKind.String ? v.GetString()! : v.GetRawText()) },
            "boolean" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Boolean, Value = isNull ? DBNull.Value : v.GetBoolean() },
            "integer" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Integer, Value = isNull ? DBNull.Value : (int)v.GetDouble() },
            "bigint" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Bigint, Value = isNull ? DBNull.Value : (long)v.GetDouble() },
            "double precision" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Double, Value = isNull ? DBNull.Value : v.GetDouble() },
            "numeric" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Numeric, Value = isNull ? DBNull.Value : v.GetDecimal() },
            "timestamptz" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.TimestampTz, Value = isNull ? DBNull.Value : DateTimeOffset.Parse(v.GetString()!).UtcDateTime },
            "date" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Date, Value = isNull ? DBNull.Value : DateOnly.Parse(v.GetString()!) },
            "jsonb" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Jsonb, Value = isNull ? DBNull.Value : v.GetRawText() },
            "text[]" => new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Array | NpgsqlDbType.Text, Value = isNull ? DBNull.Value : v.EnumerateArray().Select(e => e.GetString()).ToArray() },
            _ => throw new InvalidOperationException($"모르는 열 타입: {type}"),
        };
    }

    public ValueTask DisposeAsync() { _api?.Dispose(); return _ds?.DisposeAsync() ?? ValueTask.CompletedTask; }
}
