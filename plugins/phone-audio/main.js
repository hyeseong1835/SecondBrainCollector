// 소리 있는 구간 녹음(SecondBrain 녹음 1차). audio 소스가 파일 하나를 다 쓸 때마다 낸 정보를 구간 행으로.
// 파일 자체는 폰의 앱 전용 저장소(files/recordings/날짜/이름.m4a)에 남고, 노트북이 adb로 가져간다.
// input_*(1.1.0, schema_version 2): 녹음에 실제로 쓴 입력 장치(AudioRecord.getRoutedDevice 값 그대로). 경로를 못 읽은 파일은 null.
export default {
  start(ctx) {
    ctx.on("audio", ev => ctx.span.put("recording", {
      start: ev.start, end: ev.end, file: ev.file, bytes: ev.bytes, sha256: ev.sha256, codec: ev.codec,
      duration_ms: ev.duration_ms, mean_db: ev.mean_db, peak_db: ev.peak_db, floor_db: ev.floor_db,
      input_type: ev.input_type ?? null, input_type_name: ev.input_type_name ?? null, input_product: ev.input_product ?? null,
      input_address: ev.input_address ?? null, input_id: ev.input_id ?? null, input_changed: ev.input_changed ?? null,
    }, { key: ev.file }));
  },
  stop(ctx) {}
};
