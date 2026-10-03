package com.junshi.chat

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.chaquo.python.PyException
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * 军师 Chat（Android）。
 *
 * 结构很简单：**Python 引擎在 APK 内起一个只监听 127.0.0.1 的本地服务**，
 * WebView 加载这个服务的前端页面。这么做的理由：
 *  - 手机端和桌面端跑的是同一套 Python 代码 + 同一套前端，不存在两份实现走样的问题；
 *  - 只用标准库，所以 Chaquopy 不需要 pip 装任何东西，云端构建更稳；
 *  - 回环地址不需要任何额外权限，聊天内容只在你手机和你填的模型接口之间走。
 *
 * Key、档案、陪聊记录都存在应用私有目录（/data/data/com.junshi.chat/files），
 * 卸载即清除，其它应用读不到。
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var statusView: TextView
    private lateinit var retryButton: Button
    private val ui = Handler(Looper.getMainLooper())
    private var port: Int = 0
    private var loading = false

    companion object {
        private const val TAG = "JunshiChat"
        private const val START_PORT = 8765
        private const val PORT_TRIES = 12
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildUi()
        startEngine()
    }

    // ---------------------------------------------------------------- UI

    /** 用代码搭界面，不引 XML 布局，也就不用任何 UI 库。 */
    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }

        statusView = TextView(this).apply {
            text = "正在启动军师引擎…"
            textSize = 14f
            setPadding(36, 36, 36, 36)
        }
        retryButton = Button(this).apply {
            text = "重试"
            visibility = View.GONE
            setOnClickListener {
                visibility = View.GONE
                statusView.text = "正在重新启动…"
                startEngine()
            }
        }
        root.addView(statusView, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(retryButton, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        setContentView(root)

        web = WebView(this)
        root.addView(web, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        @SuppressLint("SetJavaScriptEnabled")
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true                  // 前端要用 localStorage 记一点偏好
            databaseEnabled = true
            cacheMode = WebSettings.LOAD_NO_CACHE     // 本地资源，别缓存住旧版本
            useWideViewPort = true
            loadWithOverviewMode = false              // 保持真实 CSS 像素，媒体查询才准
            builtInZoomControls = false
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                // 只允许留在本地服务里；外链交给系统浏览器
                if (url.startsWith("http://127.0.0.1:") || url.startsWith("http://localhost:")) {
                    return false
                }
                return try {
                    startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW,
                        android.net.Uri.parse(url)))
                    true
                } catch (e: Exception) {
                    true
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                Log.d(TAG, "JS[${msg.messageLevel()}]: ${msg.message()} @${msg.lineNumber()}")
                return true
            }
        }
        web.addJavascriptInterface(Bridge(), "JunshiAndroid")
    }

    /** 给前端一个「我跑在 APK 里」的标记，前端据此微调文案（比如剪贴板提示）。 */
    inner class Bridge {
        @android.webkit.JavascriptInterface
        fun isAndroid(): Boolean = true
    }

    // ---------------------------------------------------------------- 引擎

    private fun startEngine() {
        if (loading) return
        loading = true
        Thread {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(this))
                }
                // 数据写进应用私有目录：卸载即清，别的 App 读不到
                val dataDir = File(filesDir, "junshi-data")
                dataDir.mkdirs()

                val py: Python = Python.getInstance()
                val server: PyObject = py.getModule("junshi.android_server")
                val got: PyObject = server.callAttr(
                    "start", START_PORT, PORT_TRIES, dataDir.absolutePath)
                port = got.callAttr("__getitem__", "port").toInt()
                val error = got.callAttr("__getitem__", "error").toString()
                if (port <= 0) {
                    throw IllegalStateException(
                        error.ifBlank { "本地服务没能启动，可能是端口都被占用了" })
                }
                ui.post { onEngineReady() }
            } catch (e: PyException) {
                Log.e(TAG, "Python 启动失败", e)
                ui.post { onEngineFailed(e.message ?: e.toString()) }
            } catch (e: Exception) {
                Log.e(TAG, "引擎启动失败", e)
                ui.post { onEngineFailed(e.message ?: e.toString()) }
            } finally {
                loading = false
            }
        }.start()
    }

    private fun onEngineReady() {
        statusView.visibility = View.GONE
        retryButton.visibility = View.GONE
        web.visibility = View.VISIBLE
        web.loadUrl("http://127.0.0.1:$port/")
    }

    private fun onEngineFailed(message: String) {
        web.visibility = View.GONE
        statusView.visibility = View.VISIBLE
        retryButton.visibility = View.VISIBLE
        statusView.text = "启动失败：$message\n\n" +
            "可以点下面的「重试」。如果一直失败，请把这段信息发给开发者。"
    }

    // ---------------------------------------------------------------- 生命周期

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (this::web.isInitialized && web.canGoBack()) {
            web.goBack()
        } else {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        // Python 服务随进程退出而结束；这里只把 WebView 收干净
        if (this::web.isInitialized) {
            web.loadUrl("about:blank")
            web.destroy()
        }
    }
}
