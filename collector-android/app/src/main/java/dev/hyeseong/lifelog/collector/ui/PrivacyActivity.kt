package dev.hyeseong.lifelog.collector.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Health Connect 권한 화면이 여는 개인정보 안내. */
class PrivacyActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme(colorScheme = darkColorScheme()) {
            Text("세컨뇌 수집기는 Health Connect 데이터를 읽어 사용자 본인의 세컨뇌 서버(데이터베이스)에만 저장합니다. 다른 곳으로 보내지 않습니다.", Modifier.padding(24.dp))
        } }
    }
}
