using NAudio.CoreAudioApi;
using AudioSessionState = NAudio.CoreAudioApi.Interfaces.AudioSessionState;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// Windows 오디오 입출력 장치(MMDevice, 2026-11-16, Documents/기기.md 6절). 센서가 준 저수준 값만 낸다(기기 id·분류 없음). 이벤트 종류 셋:
///   device   { time, kind, endpoint_id, name, flow, form_factor, state, instance_id }
///            장치가 새로 생기거나 상태·이름이 바뀔 때. state = active | unplugged(잭 뽑힘·블루투스 끊김) | absent(목록에서 사라짐·꺼짐).
///            instance_id = 블루투스면 MAC이 들어 있는 BTHENUM 경로 그대로(endpoint 속성 {b3f8fa53-…},39), 없으면 컨트롤러 경로({b3f8fa53-…},2). 앞의 "{1}."도 그대로.
///   default  { time, kind, flow, role, endpoint_id, name }   기본 장치가 바뀔 때. role = console | communications, endpoint_id가 null이면 기본 장치 없음.
///   usage    { time, kind, endpoint_id, flow, name, in_use } 장치에서 오디오 세션(시스템 소리 제외)이 흐르는 중인지 바뀔 때.
/// 시작 때 지금 상태를 한 번 낸다. 장치·기본 장치 바뀜은 MMDevice 알림으로 바로 알고, 사용 여부는 3초마다 세션 상태를 읽는다.
/// </summary>
public sealed class AudioDeviceSource : ISource
{
    public string Id => "audio_device";

    public sealed record Dev(string EndpointId, string Name, string Flow, string FormFactor, string State, string? InstanceId);

    static readonly TimeSpan UsagePoll = TimeSpan.FromSeconds(3);
    static readonly TimeSpan FullRefresh = TimeSpan.FromMinutes(1);   // 알림을 놓치거나 이름만 바뀌어도 늦어도 1분 안에 따라잡는다
    static readonly (DataFlow Flow, string Name)[] Flows = [(DataFlow.Render, "render"), (DataFlow.Capture, "capture")];
    static readonly (Role Role, string Name)[] Roles = [(Role.Console, "console"), (Role.Communications, "communications")];

    private readonly Action<string>? _log;
    private readonly AutoResetEvent _wake = new(false);
    private readonly CancellationTokenSource _cts = new();
    private Action<Dictionary<string, object?>>? _emit;
    private Thread? _thread;
    private MMDeviceEnumerator? _en;
    private MMDeviceNotificationClient? _notifier;

    // 소스 스레드 안에서만 쓴다
    private readonly Dictionary<string, Dev> _known = new();
    private readonly Dictionary<string, MMDevice> _active = new();
    private readonly Dictionary<string, string?> _defaults = new();
    private readonly HashSet<string> _inUse = new();

    public AudioDeviceSource(Action<string>? log = null) { _log = log; }

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        _thread = new Thread(Run) { IsBackground = true, Name = "audio-device" };
        _thread.Start();
    }

    private void Run()
    {
        try
        {
            _en = new MMDeviceEnumerator();
            // 알림은 COM 스레드에서 온다. 읽기는 이 스레드가 하도록 깨우기만 한다. 속성 바뀜(볼륨·레벨 등으로 아주 자주 옴)은 듣지 않고, 이름 바뀜은 1분 갱신이 잡는다
            _notifier = _en.CreateNotificationClient(false);
            _notifier.DeviceStateChanged += (_, _) => _wake.Set();
            _notifier.DeviceAdded += (_, _) => _wake.Set();
            _notifier.DeviceRemoved += (_, _) => _wake.Set();
            _notifier.DefaultDeviceChanged += (_, _) => _wake.Set();
            var lastFull = DateTime.UtcNow;
            Refresh();
            while (!_cts.IsCancellationRequested)
            {
                var woke = _wake.WaitOne(UsagePoll);
                if (_cts.IsCancellationRequested) break;
                if (woke) _cts.Token.WaitHandle.WaitOne(300);   // 알림은 한꺼번에 여러 개 오므로 잠깐 모아서 읽는다
                if (woke || DateTime.UtcNow - lastFull >= FullRefresh) { Refresh(); lastFull = DateTime.UtcNow; }
                PollUsage();
            }
        }
        catch (Exception e) { _log?.Invoke($"[audio-device] 소스 중단: {e.Message}"); }
        finally
        {
            try { _notifier?.Dispose(); } catch (Exception) { }
            foreach (var d in _active.Values) d.Dispose();
            _active.Clear();
            _en?.Dispose();
        }
    }

    private void Refresh()
    {
        var cur = new Dictionary<string, Dev>();
        var fresh = new Dictionary<string, MMDevice>();
        foreach (var (flow, flowName) in Flows)
        {
            foreach (var d in _en!.EnumerateAudioEndPoints(flow, DeviceState.Active | DeviceState.Unplugged))
            {
                try
                {
                    var dev = Describe(d, flowName);
                    cur[dev.EndpointId] = dev;
                    if (dev.State == "active") { fresh[dev.EndpointId] = d; continue; }
                }
                catch (Exception e) { _log?.Invoke($"[audio-device] 장치 읽기 실패: {e.Message}"); }
                d.Dispose();
            }
        }
        foreach (var d in _active.Values) d.Dispose();
        _active.Clear();
        foreach (var (k, v) in fresh) _active[k] = v;

        var now = DateTime.UtcNow;
        foreach (var gone in _known.Values.Where(k => !cur.ContainsKey(k.EndpointId)).ToList())
        {
            if (_inUse.Remove(gone.EndpointId)) EmitUsage(now, gone, false);
            _known.Remove(gone.EndpointId);
            EmitDevice(now, gone with { State = "absent" });
        }
        foreach (var dev in cur.Values)
        {
            _known.TryGetValue(dev.EndpointId, out var old);
            if (old == dev) continue;
            if (old is { State: "active" } && dev.State != "active" && _inUse.Remove(dev.EndpointId)) EmitUsage(now, dev, false);
            _known[dev.EndpointId] = dev;
            EmitDevice(now, dev);
        }

        foreach (var (flow, flowName) in Flows)
            foreach (var (role, roleName) in Roles)
            {
                string? id = null;
                try { using var d = _en!.GetDefaultAudioEndpoint(flow, role); id = d.ID; } catch (Exception) { }
                var key = flowName + "/" + roleName;
                _defaults.TryGetValue(key, out var prev);
                if (prev == id) continue;
                _defaults[key] = id;
                _emit!(new Dictionary<string, object?>
                {
                    ["time"] = now, ["kind"] = "default", ["flow"] = flowName, ["role"] = roleName,
                    ["endpoint_id"] = id, ["name"] = id is not null && _known.TryGetValue(id, out var k) ? k.Name : null,
                });
            }
    }

    private void PollUsage()
    {
        var now = DateTime.UtcNow;
        foreach (var (id, device) in _active)
        {
            bool used;
            try { used = IsInUse(device); } catch (Exception) { continue; }   // 못 읽으면 그대로 둔다
            if (used == _inUse.Contains(id)) continue;
            if (used) _inUse.Add(id); else _inUse.Remove(id);
            if (_known.TryGetValue(id, out var dev)) EmitUsage(now, dev, used);
        }
    }

    /// <summary>시스템 소리 세션이 아닌 오디오 세션이 하나라도 흐르는 중(활성)이면 사용 중.</summary>
    static bool IsInUse(MMDevice device)
    {
        var mgr = device.AudioSessionManager;
        mgr.RefreshSessions();
        var list = mgr.Sessions;
        var used = false;
        for (var i = 0; i < list.Count; i++)
        {
            var s = list[i];
            try { if (!used && s.State == AudioSessionState.AudioSessionStateActive && !s.IsSystemSoundsSession) used = true; }
            finally { s.Dispose(); }
        }
        return used;
    }

    private void EmitDevice(DateTime now, Dev d) => _emit!(new Dictionary<string, object?>
    {
        ["time"] = now, ["kind"] = "device", ["endpoint_id"] = d.EndpointId, ["name"] = d.Name, ["flow"] = d.Flow,
        ["form_factor"] = d.FormFactor, ["state"] = d.State, ["instance_id"] = d.InstanceId,
    });

    private void EmitUsage(DateTime now, Dev d, bool inUse) => _emit!(new Dictionary<string, object?>
    {
        ["time"] = now, ["kind"] = "usage", ["endpoint_id"] = d.EndpointId, ["flow"] = d.Flow, ["name"] = d.Name, ["in_use"] = inUse,
    });

    // ---- 장치 속성 ----
    static readonly PropertyKey FormFactorKey = new(new Guid("1da5d803-d492-4edd-8c23-e0c0ffee7f0e"), 0);
    static readonly PropertyKey BtInstanceKey = new(new Guid("b3f8fa53-0004-438e-9003-51a46e139bfc"), 39);
    static readonly PropertyKey ControllerKey = new(new Guid("b3f8fa53-0004-438e-9003-51a46e139bfc"), 2);

    // EndpointFormFactor 순서(0부터)
    static readonly string[] FormFactors = ["remote_network", "speakers", "line_level", "headphones", "microphone", "headset", "handset", "digital_passthrough", "spdif", "hdmi", "unknown"];

    static string? Str(PropertyStore ps, PropertyKey key)
    {
        try { return ps.Contains(key) ? ps[key].Value as string : null; } catch (Exception) { return null; }
    }

    static Dev Describe(MMDevice d, string flowName)
    {
        var ps = d.Properties;
        var ff = "unknown";
        try { if (ps.Contains(FormFactorKey) && ps[FormFactorKey].Value is { } v) { var n = Convert.ToInt32(v); if (n >= 0 && n < FormFactors.Length) ff = FormFactors[n]; } } catch (Exception) { }
        var state = d.State == DeviceState.Active ? "active" : "unplugged";
        var instance = Str(ps, BtInstanceKey) ?? Str(ps, ControllerKey);
        return new Dev(d.ID, d.FriendlyName, flowName, ff, state, string.IsNullOrEmpty(instance) ? null : instance);
    }

    /// <summary>지금 보이는 장치(켜짐·잭 뽑힘)와 기본 장치. audio-device-test용.</summary>
    public static object Snapshot()
    {
        using var en = new MMDeviceEnumerator();
        var devices = new List<object>();
        foreach (var (flow, flowName) in Flows)
            foreach (var d in en.EnumerateAudioEndPoints(flow, DeviceState.Active | DeviceState.Unplugged))
                using (d) { var v = Describe(d, flowName); devices.Add(new { endpoint_id = v.EndpointId, name = v.Name, flow = v.Flow, form_factor = v.FormFactor, state = v.State, instance_id = v.InstanceId }); }
        var defaults = new List<object>();
        foreach (var (flow, flowName) in Flows)
            foreach (var (role, roleName) in Roles)
            {
                try { using var d = en.GetDefaultAudioEndpoint(flow, role); defaults.Add(new { flow = flowName, role = roleName, endpoint_id = d.ID, name = d.FriendlyName }); }
                catch (Exception) { defaults.Add(new { flow = flowName, role = roleName, endpoint_id = (string?)null, name = (string?)null }); }
            }
        return new { devices, defaults };
    }

    public void Dispose()
    {
        _cts.Cancel();
        _wake.Set();
        _thread?.Join(TimeSpan.FromSeconds(5));
    }
}
