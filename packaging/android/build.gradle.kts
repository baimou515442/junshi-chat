// 顶层构建脚本：只声明插件版本，具体在 app/build.gradle.kts 里应用。
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    // Chaquopy 让我们把 Python 引擎直接打进 APK：
    // 手机端和桌面端跑的是**同一套** Python 代码，不用维护第二份实现。
    id("com.chaquo.python") version "16.0.0" apply false
}
