using Windows.Devices.Bluetooth;
using Windows.Devices.Enumeration;
using Windows.Devices.Radios;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 블루투스(2026-11-16). 이 노트북에 페어링된 기기(클래식·LE)의 링크 연결·끊김, 어댑터 켜짐·꺼짐, 배터리를 낸다.
/// 안드로이드 소스(bluetooth)와 같은 이벤트 모양이라 플러그인 win-bluetooth는 phone-bluetooth와 거의 같다.
///   { kind: adapter, state: on | off }  시작할 때 한 번, 바뀔 때마다
///   { kind: acl, state: connected | disconnected, transport: bredr | le, …기기 }  기기와의 링크. 시작할 때 이미 붙어 있으면 snapshot: true
///   { kind: battery, level 0~100, …기기 }  윈도우가 PnP 속성으로 알려 줄 때만(주로 LE 기기. 클래식 이어폰은 대개 없다)
///   기기 = address("AA:BB:CC:DD:EE:FF"), address_type(public | random | null), name(윈도우 표시 이름), device_type(classic | le | dual),
///          major_class(audio_video·peripheral…), device_class(CoD &amp; 0x1FFC. 안드로이드 BluetoothClass.deviceClass와 같은 값. LE 전용은 null)
/// 페어링한 기기만 본다(주변 광고는 보지 않는다). 랜덤 주소(address_type random)는 낸 뒤 플러그인이 거른다.
/// 프로필(a2dp 등)은 윈도우가 기기별 연결 프로필을 주지 않아 내지 않는다. 오디오 출력은 win-audio-device가 맡는다.
/// 기기 목록은 30초마다 다시 읽어 새 페어링·삭제·이름 바뀜을 따라가고, 링크 변화는 ConnectionStatusChanged로 바로 받는다.
/// </summary>
public sealed class BluetoothSource : ISource
{
    public string Id => "bluetooth";

    /// <summary>PnP 속성: 블루투스 기기 배터리(%). 윈도우 설정의 기기 배터리 표시와 같은 값.</summary>
    const string BatteryKey = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2";

    public sealed record Snapshot(string Transport, string Address, string? AddressType, string? Name, bool Connected, int? MajorClass, int? DeviceClass, int? Battery);

    private sealed class Entry
    {
        public required string Id;              // DeviceInformation.Id
        public required string Transport;       // bredr | le
        public required string Address;
        public string? AddressType;
        public string? Name;
        public int? Raw;                        // 클래식 CoD 원본
        public Guid? Container;
        public IDisposable? Device;
        public bool Connected;
        public int? Battery;
    }

    private readonly Dictionary<string, Entry> _entries = new();
    private readonly object _lock = new();
    private readonly SemaphoreSlim _refreshing = new(1, 1);
    private readonly Action<string>? _log;
    private readonly CancellationTokenSource _cts = new();
    private Action<Dictionary<string, object?>>? _emit;
    private Radio? _radio;
    private System.Threading.Timer? _timer;
    private bool _first = true;

    public BluetoothSource(Action<string>? log = null) { _log = log; }

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        _ = Task.Run(async () =>
        {
            await StartRadioAsync();
            await RefreshAsync();
            if (_cts.IsCancellationRequested) return;
            _timer = new System.Threading.Timer(_ => _ = RefreshAsync(), null, TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(30));
        });
    }

    private async Task StartRadioAsync()
    {
        try
        {
            var radios = await Radio.GetRadiosAsync();
            _radio = radios.FirstOrDefault(r => r.Kind == RadioKind.Bluetooth);
            if (_radio is null) { _log?.Invoke("[bluetooth] 블루투스 라디오가 없습니다"); return; }
            _radio.StateChanged += OnRadioChanged;
            EmitAdapter(_radio.State);
        }
        catch (Exception e) { _log?.Invoke($"[bluetooth] 라디오 읽기 실패: {e.Message}"); }
    }

    private void OnRadioChanged(Radio r, object _) => EmitAdapter(r.State);

    private void EmitAdapter(RadioState s)
    {
        var state = s switch { RadioState.On => "on", RadioState.Off or RadioState.Disabled => "off", _ => null };
        if (state is null) return;
        _emit?.Invoke(new Dictionary<string, object?> { ["time"] = DateTime.UtcNow, ["kind"] = "adapter", ["state"] = state });
        if (state == "off")
        {
            // 윈도우가 끊김 이벤트를 따로 주지 않을 수 있다. 다시 켜졌을 때 연결을 새로 알 수 있도록 지금 붙어 있던 것을 끊긴 것으로 친다
            List<Entry> was;
            lock (_lock)
            {
                was = _entries.Values.Where(x => x.Connected).ToList();
                foreach (var e in was) { e.Connected = false; e.Battery = null; }
            }
            foreach (var e in was) EmitAcl(e, false, false);
        }
        else _ = RefreshAsync();   // 켜졌을 때 이미 붙은 기기를 바로 반영
    }

    /// <summary>페어링한 기기 목록을 다시 읽어 새로 생긴 것·없어진 것·링크가 달라진 것·배터리를 반영한다.</summary>
    private async Task RefreshAsync()
    {
        if (_cts.IsCancellationRequested || !await _refreshing.WaitAsync(0)) return;
        try
        {
            var infos = new Dictionary<string, (DeviceInformation Info, bool Le)>();
            string[] props = ["System.Devices.Aep.ContainerId"];
            foreach (var info in await DeviceInformation.FindAllAsync(BluetoothDevice.GetDeviceSelectorFromPairingState(true), props)) infos[info.Id] = (info, false);
            foreach (var info in await DeviceInformation.FindAllAsync(BluetoothLEDevice.GetDeviceSelectorFromPairingState(true), props)) infos[info.Id] = (info, true);

            var snapshot = _first; _first = false;
            foreach (var (id, (info, le)) in infos)
            {
                Entry? e;
                lock (_lock) _entries.TryGetValue(id, out e);
                if (e is null) e = await AddAsync(info, le);
                if (e is null) continue;
                lock (_lock) e.Name = Clean(info.Name) ?? e.Name;
                CheckStatus(e, snapshot);
            }
            List<Entry> gone;
            lock (_lock) gone = _entries.Values.Where(x => !infos.ContainsKey(x.Id)).ToList();
            foreach (var e in gone) Remove(e);

            List<Entry> connected;
            lock (_lock) connected = _entries.Values.Where(x => x.Connected).ToList();
            foreach (var e in connected) await ReadBatteryAsync(e);
        }
        catch (Exception e) { _log?.Invoke($"[bluetooth] 기기 목록 읽기 실패: {e.Message}"); }
        finally { _refreshing.Release(); }
    }

    private async Task<Entry?> AddAsync(DeviceInformation info, bool le)
    {
        try
        {
            Entry e;
            if (le)
            {
                var d = await BluetoothLEDevice.FromIdAsync(info.Id);
                if (d is null) return null;
                e = new Entry
                {
                    Id = info.Id, Transport = "le", Address = Format(d.BluetoothAddress),
                    AddressType = d.BluetoothAddressType switch { BluetoothAddressType.Public => "public", BluetoothAddressType.Random => "random", _ => null },
                    Name = Clean(info.Name) ?? Clean(d.Name), Device = d,
                };
                d.ConnectionStatusChanged += (_, _) => CheckStatus(e, false);
            }
            else
            {
                var d = await BluetoothDevice.FromIdAsync(info.Id);
                if (d is null) return null;
                var raw = (int)d.ClassOfDevice.RawValue;
                e = new Entry
                {
                    Id = info.Id, Transport = "bredr", Address = Format(d.BluetoothAddress), AddressType = "public",
                    Name = Clean(info.Name) ?? Clean(d.Name), Raw = raw == 0 ? null : raw, Device = d,
                };
                d.ConnectionStatusChanged += (_, _) => CheckStatus(e, false);
            }
            if (info.Properties.TryGetValue("System.Devices.Aep.ContainerId", out var c) && c is Guid g) e.Container = g;
            lock (_lock) _entries[info.Id] = e;
            return e;
        }
        catch (Exception ex) { _log?.Invoke($"[bluetooth] 기기 열기 실패({info.Name}): {ex.Message}"); return null; }
    }

    private void Remove(Entry e)
    {
        bool was;
        lock (_lock) { was = e.Connected; e.Connected = false; _entries.Remove(e.Id); }
        if (was) EmitAcl(e, false, false);
        e.Device?.Dispose();
    }

    /// <summary>링크 상태가 기억한 것과 다르면 acl 이벤트를 낸다. 이벤트와 주기 읽기가 같이 불러도 한 번만 낸다.</summary>
    private void CheckStatus(Entry e, bool snapshot)
    {
        bool now;
        try
        {
            now = e.Device switch
            {
                BluetoothDevice d => d.ConnectionStatus == BluetoothConnectionStatus.Connected,
                BluetoothLEDevice d => d.ConnectionStatus == BluetoothConnectionStatus.Connected,
                _ => false,
            };
        }
        catch (Exception) { return; }
        lock (_lock)
        {
            if (e.Connected == now) return;
            e.Connected = now;
            if (!now) e.Battery = null;
        }
        EmitAcl(e, now, snapshot && now);
    }

    private void EmitAcl(Entry e, bool connected, bool snapshot)
    {
        var ev = Device("acl", e);
        ev["state"] = connected ? "connected" : "disconnected";
        ev["transport"] = e.Transport;
        if (snapshot) ev["snapshot"] = true;
        _emit?.Invoke(ev);
    }

    private async Task ReadBatteryAsync(Entry e)
    {
        if (e.Container is not { } cid) return;
        int? level = null;
        try
        {
            var nodes = await DeviceInformation.FindAllAsync($"System.Devices.ContainerId:=\"{{{cid}}}\"", [BatteryKey], DeviceInformationKind.Device);
            foreach (var n in nodes)
                if (n.Properties.TryGetValue(BatteryKey, out var v) && v is not null && int.TryParse(Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture), out var p) && p is >= 0 and <= 100) level = p;
        }
        catch (Exception) { return; }
        if (level is null) return;
        lock (_lock) { if (e.Battery == level || !e.Connected) return; e.Battery = level; }
        var ev = Device("battery", e);
        ev["level"] = level;
        _emit?.Invoke(ev);
    }

    /// <summary>같은 주소의 클래식·LE 항목을 한 기기로 보고 기기 필드를 만든다.</summary>
    private Dictionary<string, object?> Device(string kind, Entry e)
    {
        lock (_lock)
        {
            var same = _entries.Values.Where(x => x.Address == e.Address).Append(e).DistinctBy(x => x.Id).ToList();
            var classic = same.FirstOrDefault(x => x.Transport == "bredr");
            var hasLe = same.Any(x => x.Transport == "le");
            var raw = classic?.Raw;
            return new Dictionary<string, object?>
            {
                ["time"] = DateTime.UtcNow, ["kind"] = kind,
                ["address"] = e.Address, ["address_type"] = e.AddressType,
                ["name"] = e.Name ?? same.Select(x => x.Name).FirstOrDefault(n => n != null),
                ["device_type"] = classic != null && hasLe ? "dual" : e.Transport == "bredr" ? "classic" : "le",
                ["major_class"] = raw is { } r ? MajorName(r & 0x1F00) : null,
                ["device_class"] = raw is { } r2 ? r2 & 0x1FFC : null,
            };
        }
    }

    static string MajorName(int major) => major switch
    {
        0x0000 => "misc", 0x0100 => "computer", 0x0200 => "phone", 0x0300 => "networking", 0x0400 => "audio_video", 0x0500 => "peripheral",
        0x0600 => "imaging", 0x0700 => "wearable", 0x0800 => "toy", 0x0900 => "health", 0x1F00 => "uncategorized", _ => $"unknown_{major}",
    };

    static string? Clean(string? s) => string.IsNullOrWhiteSpace(s) ? null : s.Trim();

    static string Format(ulong a) => string.Join(":", Enumerable.Range(0, 6).Select(i => ((a >> (40 - 8 * i)) & 0xFF).ToString("X2")));

    /// <summary>시험·진단용: 지금 페어링된 기기와 상태를 한 번 읽는다(이벤트 없이).</summary>
    public static async Task<List<Snapshot>> ReadAsync()
    {
        var o = new List<Snapshot>();
        var src = new BluetoothSource();
        src._emit = _ => { };
        await src.RefreshAsync();
        lock (src._lock)
            foreach (var e in src._entries.Values.OrderBy(x => x.Address).ThenBy(x => x.Transport))
                o.Add(new Snapshot(e.Transport, e.Address, e.AddressType, e.Name, e.Connected, e.Raw is { } r ? r & 0x1F00 : null, e.Raw is { } r2 ? r2 & 0x1FFC : null, e.Battery));
        src.Dispose();
        return o;
    }

    public void Dispose()
    {
        _cts.Cancel();
        _timer?.Dispose();
        if (_radio is not null) _radio.StateChanged -= OnRadioChanged;
        lock (_lock) { foreach (var e in _entries.Values) e.Device?.Dispose(); _entries.Clear(); }
    }
}
