using NAudio.CoreAudioApi;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 녹음에 쓴 입력 장치의 저수준 값(2026-11-16, Documents/기기.md 6절). 기기 id가 아니라 Windows가 준 값 그대로:
/// endpoint ID(MMDevice ID), 이름(FriendlyName), 장치 인스턴스 ID(instance_id).
/// instance_id는 win-audio-device 플러그인(AudioDeviceSource)과 같은 값이다: 블루투스면 MAC이 들어 있는 BTHENUM 경로(endpoint 속성 {b3f8fa53-…},39),
/// 없으면 컨트롤러 경로({b3f8fa53-…},2. USB 마이크는 USB\VID_xxxx&amp;PID_xxxx&amp;MI_00\…, 내장 마이크는 INTELAUDIO\CTLR_DEV_…). 앞의 "{1}."도 그대로. 못 읽으면 null.
/// 구간 행 열 input_device_id·input_device_name·input_device_instance_id로 간다.
/// </summary>
public sealed record InputDeviceInfo(string Id, string Name, string? InstanceId)
{
    static readonly PropertyKey BtInstanceKey = new(new Guid("b3f8fa53-0004-438e-9003-51a46e139bfc"), 39);
    static readonly PropertyKey ControllerKey = new(new Guid("b3f8fa53-0004-438e-9003-51a46e139bfc"), 2);

    public static InputDeviceInfo Of(MMDevice device)
    {
        var ps = device.Properties;
        var instance = Str(ps, BtInstanceKey) ?? Str(ps, ControllerKey);
        string name;
        try { name = device.FriendlyName; } catch (Exception) { name = ""; }
        return new(device.ID, name, string.IsNullOrEmpty(instance) ? null : instance);
    }

    static string? Str(PropertyStore ps, PropertyKey key)
    {
        try { return ps.Contains(key) ? ps[key].Value as string : null; } catch (Exception) { return null; }
    }

    /// <summary>행에 더할 열.</summary>
    public Dictionary<string, object?> Columns() => new()
    {
        ["input_device_id"] = Id, ["input_device_name"] = Name, ["input_device_instance_id"] = InstanceId,
    };
}
