// 소리 있는 구간 녹음(SecondBrain 녹음 1차). audio 소스가 파일 하나를 다 쓸 때마다 낸 정보를 구간 행으로.
// 파일 자체는 폰의 앱 전용 저장소(files/recordings/날짜/이름.m4a)에 남고, 노트북이 adb로 가져간다.
export default {
  start(ctx) {
    ctx.on("audio", ev => ctx.span.put("recording", {
      start: ev.start, end: ev.end, file: ev.file, bytes: ev.bytes, sha256: ev.sha256, codec: ev.codec,
      duration_ms: ev.duration_ms, mean_db: ev.mean_db, peak_db: ev.peak_db, floor_db: ev.floor_db,
    }, { key: ev.file }));
  },
  stop(ctx) {}
};
