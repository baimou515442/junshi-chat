import com.chaquo.python.PythonPlugin

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.junshi.chat"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.junshi.chat"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"

        // Chaquopy 的 Python 3.12 运行时只有这两个 ABI 可用：
        //   构建时会明确报「Python 3.12 is not available for the ABI 'armeabi-v7a'」，
        //   把它加回去会直接构建失败（踩过）。
        //   arm64-v8a 覆盖 2017 年之后几乎所有手机；x86_64 是为了模拟器。
        //   因此本 APK 不支持 32 位老机型。
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            // 用 debug 签名，这样 GitHub Actions 不需要你提供密钥库就能产出可安装的 APK。
            // 自己正式发布时再换成自己的 keystore。
            signingConfig = signingConfigs.getByName("debug")
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        // 我们只用标准库，pip 一个都不用装——这正是当初坚持不用第三方包的原因。
        pip { }
    }
    sourceSets {
        getByName("main") {
            // 把 Ubuntu 侧那套 Python 引擎整包搬进来（含 junshi/kb 下的 44 份知识库）
            srcDir("src/main/python")
        }
    }
}

dependencies {
    // 故意不引入任何第三方依赖：只用 Android 框架自带的 WebView 和网络类。
    // 依赖越少，云端构建越不容易因为某个库的版本变动而失败。
}
