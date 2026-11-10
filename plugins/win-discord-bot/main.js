// 디스코드 봇 녹음(켰을 때만). discord_bot 소스가 사람별 파일 하나를 다 쓸 때마다 구간 행으로.
// 파일은 %USERPROFILE%\SecondBrainRecordings\discord-bot\<날짜>\<이름>.m4a, 옆 .json에 화자 정보. ID는 문자열(JS 숫자 범위 밖).
export default {
  start(ctx) {
    ctx.on("discord_bot", ev => ctx.span.put("recording", {
      start: ev.start, end: ev.end, file: ev.file, bytes: ev.bytes, sha256: ev.sha256, codec: ev.codec,
      duration_ms: ev.duration_ms, mean_db: ev.mean_db, peak_db: ev.peak_db,
      user_id: ev.user_id, speaker: ev.speaker, guild_id: ev.guild_id, channel_id: ev.channel_id,
    }, { key: ev.file }));
  },
  stop(ctx) {}
};
