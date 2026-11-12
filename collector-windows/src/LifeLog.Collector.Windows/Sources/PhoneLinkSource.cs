using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using LifeLog.Core;
using Microsoft.Win32;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 폰↔노트북 블루투스 링크(BLE GATT). 노트북이 GATT 서버(주변기기)로 광고하고 폰 수집기가 클라이언트로 붙는다.
/// 폰이 연결된 상태에서 RSSI를 읽어 보내고(노트북 쪽 Windows는 연결 상대의 RSSI를 못 읽는다), 시계 차이를 잰다.
/// 메시지는 UTF-8 JSON 한 개씩 { t: 종류, … }. 폰→노트북은 rx 특성에 쓰기, 노트북→폰은 tx 특성 알림.
///   { time, kind: link, state: connected | disconnected }            폰이 tx 알림을 구독/해제(=링크가 붙음/끊김)
///   { time, kind: rssi, rssi }                                       폰이 잰 RSSI 하나(time = 잰 시각, 노트북 시계)
///   { time, kind: clock, offset_ms, rtt_ms }                         폰 시계 − 노트북 시계(ms), 왕복 시간
///   { time, kind: advertising, state: on | off, reason }             광고 켬·끔(잠자기 때 끈다)
///   { time, kind: message, t, body }                                 모르는 종류(나중 기능용 자리)
/// 보안: 두 특성 모두 암호화(LE 페어링)가 있어야 읽고 쓸 수 있다. 처음 한 번 페어링 확인이 뜰 수 있다.
/// 상태 파일 phone-link-status.json(설정 폴더): 보정 명령(phone-link-calibrate)이 읽는다.
/// </summary>
public sealed class PhoneLinkSource : ISource
{
    public static readonly Guid ServiceUuid = Guid.Parse("5b1e0001-7c3a-4f8e-9a1d-2f6c8e4b9d10");
    public static readonly Guid RxUuid = Guid.Parse("5b1e0002-7c3a-4f8e-9a1d-2f6c8e4b9d10");   // 폰 → 노트북 (write)
    public static readonly Guid TxUuid = Guid.Parse("5b1e0003-7c3a-4f8e-9a1d-2f6c8e4b9d10");   // 노트북 → 폰 (notify)
    public static string StatusPath => Path.Combine(Config.Dir, "phone-link-status.json");

    public string Id => "phone_link";
    private readonly Action<string> _log;
    private Action<Dictionary<string, object?>>? _emit;
    private GattServiceProvider? _provider;
    private GattLocalCharacteristic? _tx;
    private bool _advertising, _connected, _suspended;
    private readonly object _lock = new();
    private readonly List<(long ms, int rssi)> _recent = new();   // 상태 파일용 최근 RSSI(최대 120개)
    private System.Threading.Timer? _statusTimer;

    public PhoneLinkSource(Action<string> log) { _log = log; }

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        SystemEvents.PowerModeChanged += OnPower;
        _ = Task.Run(StartServerAsync);
        _statusTimer = new System.Threading.Timer(_ => WriteStatus(), null, TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(2));
    }

    private async Task StartServerAsync()
    {
        try
        {
            var adapter = await BluetoothAdapter.GetDefaultAsync();
            if (adapter is null || !adapter.IsPeripheralRoleSupported) { _log("[phone_link] 이 노트북 블루투스는 주변기기(광고) 역할을 지원하지 않습니다"); return; }
            var r = await GattServiceProvider.CreateAsync(ServiceUuid);
            if (r.Error != BluetoothError.Success) { _log($"[phone_link] GATT 서비스 만들기 실패: {r.Error}"); return; }
            var p = r.ServiceProvider;
            var rx = await p.Service.CreateCharacteristicAsync(RxUuid, new GattLocalCharacteristicParameters
            {
                CharacteristicProperties = GattCharacteristicProperties.Write | GattCharacteristicProperties.WriteWithoutResponse,
                WriteProtectionLevel = GattProtectionLevel.EncryptionRequired,
                UserDescription = "SecondBrain rx",
            });
            if (rx.Error != BluetoothError.Success) { _log($"[phone_link] rx 특성 실패: {rx.Error}"); return; }
            rx.Characteristic.WriteRequested += OnWrite;
            var tx = await p.Service.CreateCharacteristicAsync(TxUuid, new GattLocalCharacteristicParameters
            {
                CharacteristicProperties = GattCharacteristicProperties.Notify | GattCharacteristicProperties.Read,
                ReadProtectionLevel = GattProtectionLevel.EncryptionRequired,
                UserDescription = "SecondBrain tx",
            });
            if (tx.Error != BluetoothError.Success) { _log($"[phone_link] tx 특성 실패: {tx.Error}"); return; }
            _tx = tx.Characteristic;
            _tx.SubscribedClientsChanged += (c, _) => OnSubscribers(c.SubscribedClients.Count);
            _tx.ReadRequested += async (_, a) =>
            {
                using var d = a.GetDeferral();
                var req = await a.GetRequestAsync();
                req?.RespondWithValue(Buffer(new JsonObject { ["t"] = "hello", ["s"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() }));
            };
            p.AdvertisementStatusChanged += (_, a) => { if (a.Error != BluetoothError.Success) _log($"[phone_link] 광고 상태 {a.Status} 오류 {a.Error}"); };
            _provider = p;
            if (!_suspended) StartAdvertising("start");
        }
        catch (Exception e) { _log($"[phone_link] 시작 실패: {e.Message}"); }
    }

    private void StartAdvertising(string reason)
    {
        lock (_lock)
        {
            if (_provider is null || _advertising) return;
            try
            {
                // 광고에는 서비스 UUID만 실린다. 폰은 이 UUID로 찾는다
                _provider.StartAdvertising(new GattServiceProviderAdvertisingParameters { IsConnectable = true, IsDiscoverable = true });
                _advertising = true;
                Emit(new() { ["kind"] = "advertising", ["state"] = "on", ["reason"] = reason });
            }
            catch (Exception e) { _log($"[phone_link] 광고 시작 실패: {e.Message}"); }
        }
    }

    private void StopAdvertising(string reason)
    {
        lock (_lock)
        {
            if (_provider is null || !_advertising) return;
            try { _provider.StopAdvertising(); } catch (Exception e) { _log($"[phone_link] 광고 끄기 실패: {e.Message}"); }
            _advertising = false;
            Emit(new() { ["kind"] = "advertising", ["state"] = "off", ["reason"] = reason });
        }
        OnSubscribers(0);
    }

    // 잠자기에 들어가면 광고를 끈다(가방 속에서 연결 시도로 깨어 있지 않게). 깨어나면 다시 켠다
    private void OnPower(object sender, PowerModeChangedEventArgs e)
    {
        if (e.Mode == PowerModes.Suspend) { _suspended = true; StopAdvertising("sleep"); }
        else if (e.Mode == PowerModes.Resume) { _suspended = false; StartAdvertising("wake"); }
    }

    private void OnSubscribers(int count)
    {
        var now = count > 0;
        lock (_lock)
        {
            if (now == _connected) return;
            _connected = now;
        }
        Emit(new() { ["kind"] = "link", ["state"] = now ? "connected" : "disconnected" });
    }

    private async void OnWrite(GattLocalCharacteristic sender, GattWriteRequestedEventArgs args)
    {
        using var deferral = args.GetDeferral();
        try
        {
            var req = await args.GetRequestAsync();
            if (req is null) return;
            var reader = DataReader.FromBuffer(req.Value);
            var bytes = new byte[req.Value.Length];
            reader.ReadBytes(bytes);
            if (req.Option == GattWriteOption.WriteWithResponse) req.Respond();
            OnSubscribers(1);   // 쓰기가 왔으면 붙어 있다(구독 알림이 늦을 때)
            await HandleAsync(Encoding.UTF8.GetString(bytes), args.Session);
        }
        catch (Exception e) { _log($"[phone_link] 메시지 처리 실패: {e.Message}"); }
    }

    private async Task HandleAsync(string text, GattSession session)
    {
        var msg = JsonNode.Parse(text)?.AsObject();
        if (msg is null) return;
        var t = (string?)msg["t"];
        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        switch (t)
        {
            case "rssi":
                // { t: rssi, s: [[몇 ms 전, rssi], …] } 폰이 묶어 보낸다. 시각은 노트북 시계로 되돌려 매긴다(폰 시계와 무관)
                foreach (var item in msg["s"]?.AsArray() ?? [])
                {
                    var a = item!.AsArray();
                    long ago = (long)a[0]!; int rssi = (int)a[1]!;
                    var at = nowMs - ago;
                    lock (_recent) { _recent.Add((at, rssi)); if (_recent.Count > 120) _recent.RemoveAt(0); }
                    Emit(new() { ["time"] = DateTimeOffset.FromUnixTimeMilliseconds(at).ToString("o"), ["kind"] = "rssi", ["rssi"] = rssi });
                }
                break;
            case "ping":
                // 시계 맞추기: 폰이 보낸 c0를 그대로, 노트북 시각 s를 붙여 바로 돌려준다
                await NotifyAsync(new JsonObject { ["t"] = "pong", ["id"] = msg["id"]?.DeepClone(), ["c0"] = msg["c0"]?.DeepClone(), ["s"] = nowMs }, session);
                break;
            case "clock":
                Emit(new() { ["kind"] = "clock", ["offset_ms"] = (long?)msg["offset_ms"], ["rtt_ms"] = (long?)msg["rtt_ms"] });
                break;
            default:
                Emit(new() { ["kind"] = "message", ["t"] = t, ["body"] = text });
                break;
        }
    }

    private async Task NotifyAsync(JsonObject o, GattSession session)
    {
        var tx = _tx;
        if (tx is null) return;
        var client = tx.SubscribedClients.FirstOrDefault(c => c.Session.DeviceId.Id == session.DeviceId.Id) ?? tx.SubscribedClients.FirstOrDefault();
        if (client is null) return;
        await tx.NotifyValueAsync(Buffer(o), client);
    }

    private static IBuffer Buffer(JsonObject o)
    {
        var w = new DataWriter();
        w.WriteBytes(Encoding.UTF8.GetBytes(o.ToJsonString()));
        return w.DetachBuffer();
    }

    private void Emit(Dictionary<string, object?> ev)
    {
        if (!ev.ContainsKey("time")) ev["time"] = DateTimeOffset.UtcNow.ToString("o");
        _emit?.Invoke(ev);
    }

    private void WriteStatus()
    {
        try
        {
            List<(long ms, int rssi)> recent;
            lock (_recent) recent = _recent.ToList();
            var o = new
            {
                time = DateTimeOffset.UtcNow.ToString("o"),
                advertising = _advertising,
                connected = _connected,
                recent = recent.Select(r => new[] { r.ms, r.rssi }).ToList(),
            };
            var tmp = StatusPath + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(o));
            File.Move(tmp, StatusPath, true);
        }
        catch (IOException) { }
    }

    public void Dispose()
    {
        SystemEvents.PowerModeChanged -= OnPower;
        _statusTimer?.Dispose();
        StopAdvertising("stop");
        try { File.Delete(StatusPath); } catch (IOException) { }
    }
}
