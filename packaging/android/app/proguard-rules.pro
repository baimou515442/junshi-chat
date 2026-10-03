# release 目前 isMinifyEnabled=false，这份规则先留着备用。
# 关键一条：JS 桥接方法不能被混淆，否则前端调不到。
-keepclassmembers class com.junshi.chat.MainActivity$Bridge {
    @android.webkit.JavascriptInterface <methods>;
}
-keep class com.chaquo.python.** { *; }
