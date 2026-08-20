package com.siming.mobile

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.lifecycle.lifecycleScope
import com.google.zxing.client.android.Intents
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.siming.mobile.data.MAX_NOVEL_IMPORT_BYTES
import com.siming.mobile.data.MobileExportFile
import com.siming.mobile.data.MobileNovelImportFile
import com.siming.mobile.ui.MainViewModel
import com.siming.mobile.ui.PortraitCaptureActivity
import com.siming.mobile.ui.SimingApp
import com.siming.mobile.ui.SimingTheme
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    private val qrScanner = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let(viewModel::acceptPairingQr)
    }


private var pendingExport: MobileExportFile? = null
private val exportSaver = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
    val file = pendingExport
    pendingExport = null
    val uri = result.data?.data
    if (result.resultCode != Activity.RESULT_OK || uri == null || file == null) return@registerForActivityResult
    lifecycleScope.launch {
        runCatching {
            withContext(Dispatchers.IO) {
                contentResolver.openOutputStream(uri, "w")?.use { output ->
                    output.write(file.bytes)
                } ?: error("无法打开导出位置")
            }
        }.onSuccess { viewModel.reportNotice("已导出：${file.filename}") }
            .onFailure { viewModel.reportError(it.message ?: "导出文件写入失败") }
    }
}

private fun saveExport(file: MobileExportFile) {
    pendingExport = file
    exportSaver.launch(
        Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(file.mimeType)
            .putExtra(Intent.EXTRA_TITLE, file.filename),
    )
}

private var importCallback: ((MobileNovelImportFile) -> Unit)? = null

    private suspend fun readImportFile(uri: Uri): MobileNovelImportFile = withContext(Dispatchers.IO) {
        val name = contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        } ?: "导入作品.txt"
        val bytes = contentResolver.openInputStream(uri)?.use { input ->
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(32 * 1024)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                require(total <= MAX_NOVEL_IMPORT_BYTES) {
                    "单个导入文件不能超过 20 MiB"
                }
                output.write(buffer, 0, count)
            }
            output.toByteArray()
        } ?: error("无法读取选择的文件")
        MobileNovelImportFile(name, bytes)
    }

    private val textPicker = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        val callback = importCallback
        importCallback = null
        if (uri == null || callback == null) return@registerForActivityResult
        lifecycleScope.launch {
            runCatching { readImportFile(uri) }
                .onSuccess(callback)
                .onFailure { viewModel.reportError(it.message ?: "无法读取选择的文件") }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SimingTheme {
                SimingApp(
                    viewModel = viewModel,
                    onScanQr = {
                        qrScanner.launch(
                            ScanOptions()
                                .setCaptureActivity(PortraitCaptureActivity::class.java)
                                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                                .setPrompt("扫描电脑上司命生成的一次性配对二维码")
                                .setBeepEnabled(false)
                                .setOrientationLocked(true)
                                .addExtra(Intents.Scan.SCAN_TYPE, Intents.Scan.MIXED_SCAN),
                        )
                    },
                    onPickText = { callback ->
                        importCallback = callback
                        textPicker.launch("text/*")
                    },
                    onSaveExport = ::saveExport,
                )
            }
        }
    }
}
