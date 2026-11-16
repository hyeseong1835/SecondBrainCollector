// 노트북 마이크 상시 녹음. laptop_mic 소스(WASAPI 입력)가 파일 하나를 다 쓸 때마다 낸 정보를 구간 행으로.
// 파일은 %USERPROFILE%\SecondBrainRecordings\laptop-mic\<날짜>\<이름>.m4a에 남는다(file은 그 아래 상대 경로). 열은 phone-audio·win-discord-output과 같다.
// input_device_*(1.1.0, schema_version 2): 녹음에 쓴 입력 장치의 endpoint ID·이름·장치 인스턴스 ID(Windows가 준 값 그대로).
export default {
  start(ctx) {
    ctx.on("laptop_mic", ev => ctx.span.put("recording", {
      start: ev.start, end: ev.end, file: ev.file, bytes: ev.bytes, sha256: ev.sha256, codec: ev.codec,
      duration_ms: ev.duration_ms, mean_db: ev.mean_db, peak_db: ev.peak_db, floor_db: ev.floor_db,
      input_device_id: ev.input_device_id ?? null, input_device_name: ev.input_device_name ?? null, input_device_instance_id: ev.input_device_instance_id ?? null,
    }, { key: ev.file }));
  },
  stop(ctx) {}
};
