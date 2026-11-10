plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// 삼성 헬스 Data SDK: 삼성 개발자 사이트에서 받은 aar를 app/libs/에 두면 실제 구현(src/shd)을, 없으면 빈 구현(src/noshd)을 쓴다
val shdAar = fileTree("libs") { include("samsung-health-data-api*.aar") }

android {
    namespace = "dev.hyeseong.lifelog.collector"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.hyeseong.lifelog.collector"
        minSdk = 31   // LocationManager.requestLocationUpdates(LocationRequest) API 31. 대상은 S26U(Android 16)
        targetSdk = 36
        versionCode = 3
        versionName = "0.3.0"
    }

    // 첫 플러그인은 저장소 plugins/를 assets로 묶어 첫 실행 때 filesDir/plugins로 푼다(GitHub URL 설치는 나중)
    sourceSets.getByName("main").assets.directories.add("../../plugins")
    // 휴대폰 대시보드: 컴퓨터 대시보드와 같은 셸(web/*)과 SDK(lifelog-sdk.js)를 assets 맨 위에 둔다
    sourceSets.getByName("main").assets.directories.add("../../dashboard-windows/src/LifeLog.Dashboard.Windows/web")
    sourceSets.getByName("main").assets.directories.add("../../sdk")
    sourceSets.getByName("main").kotlin.directories.add(if (shdAar.isEmpty) "src/noshd/java" else "src/shd/java")

    buildTypes {
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    testOptions { unitTests.isReturnDefaultValues = true }
    packaging {
        resources.excludes += setOf("META-INF/INDEX.LIST", "META-INF/DEPENDENCIES", "META-INF/io.netty.versions.properties")
        // 16KB 페이지 기기: APK 안에 그대로 둔 .so는 16KB 경계에 놓여야 한다. 설치 때 풀어 쓰면 zip 배치와 무관하다(.so는 모두 16KB 정렬 빌드)
        jniLibs.useLegacyPackaging = true
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.02.01"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.health.connect:connect-client:1.1.0")
    implementation("androidx.security:security-crypto:1.1.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    implementation("org.postgresql:postgresql:42.2.8")      // Postgres 직접 연결. 42.2.9+는 maxResultBuffer 파서가 java.lang.management를 써서 Android에서 못 붙는다
    if (!shdAar.isEmpty) {
        implementation(shdAar)                                   // 삼성 헬스 Data SDK(있을 때만)
        implementation("org.jetbrains.kotlin:kotlin-parcelize-runtime:2.2.10") // SDK의 @Parcelize 클래스가 런타임에 찾는다
    }
    implementation("androidx.webkit:webkit:1.12.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")   // 단위 테스트에서 android.jar의 빈 org.json 대신               // 대시보드 WebView의 웹 메시지 리스너
    implementation("wang.harlon.quickjs:wrapper-android:3.2.0") // JS 수집기 런타임(QuickJS). 16KB 페이지 정렬된 .so
}
