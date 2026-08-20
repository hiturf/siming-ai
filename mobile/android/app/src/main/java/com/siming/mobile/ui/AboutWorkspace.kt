package com.siming.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.Code
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.siming.mobile.BuildConfig
import com.siming.mobile.R
import java.time.Year

internal const val SIMING_REPOSITORY_URL = "https://github.com/teangtang1122/siming-ai"
internal const val SIMING_RELEASES_URL = "$SIMING_REPOSITORY_URL/releases"
internal const val SIMING_ISSUES_URL = "$SIMING_REPOSITORY_URL/issues/new/choose"
internal const val SIMING_QQ_GROUP = "814283606"

internal data class MobileAboutPrinciple(
    val number: String,
    val title: String,
    val detail: String,
)

internal val mobileAboutPrinciples = listOf(
    MobileAboutPrinciple(
        number = "01",
        title = "作品属于作者",
        detail = "正文、资料与离线修改优先保存在你的设备，不会被锁进一段不可迁移的聊天记录。",
    ),
    MobileAboutPrinciple(
        number = "02",
        title = "事实先于生成",
        detail = "大纲、角色状态与世界规则共同约束写作，让模型先理解故事已经发生了什么。",
    ),
    MobileAboutPrinciple(
        number = "03",
        title = "边界必须透明",
        detail = "使用哪个模型、发送哪些上下文以及同步到哪里，都应当让作者看得见、能选择。",
    ),
)

internal fun mobileAboutVersionLabel(versionName: String): String = "v$versionName"

private data class MobileAboutLink(
    val label: String,
    val detail: String,
    val url: String,
    val icon: ImageVector,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun MobileAboutWorkspace(onBack: () -> Unit) {
    val uriHandler = LocalUriHandler.current
    val links = listOf(
        MobileAboutLink("源码与文档", "GitHub Repository", SIMING_REPOSITORY_URL, Icons.Outlined.Code),
        MobileAboutLink("版本记录", "Releases & Changelog", SIMING_RELEASES_URL, Icons.Outlined.History),
        MobileAboutLink("反馈问题", "Issues & Suggestions", SIMING_ISSUES_URL, Icons.Outlined.Info),
    )

    BackHandler(onBack = onBack)

    Scaffold(
        containerColor = SimingPaper,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("关于我们", fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回设置")
                    }
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = SimingPaper),
            )
        },
    ) { scaffoldPadding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(scaffoldPadding),
            contentPadding = PaddingValues(18.dp, 10.dp, 18.dp, 36.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            item {
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(28.dp),
                    color = SimingSurfaceRaised,
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Column(
                        modifier = Modifier.padding(22.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Image(
                            painter = painterResource(R.drawable.ic_siming_pc),
                            contentDescription = "司命应用图标",
                            modifier = Modifier
                                .size(88.dp)
                                .shadow(8.dp, RoundedCornerShape(22.dp))
                                .clip(RoundedCornerShape(22.dp)),
                        )
                        AboutKicker("ABOUT SIMING · 关于我们")
                        Text(
                            text = "让长篇故事，\n记得自己走过的路。",
                            style = MaterialTheme.typography.headlineSmall,
                            fontWeight = FontWeight.SemiBold,
                            lineHeight = 32.sp,
                        )
                        Text(
                            text = "司命是一款免费、开源、本地优先的 AI 长篇创作工作台。AI 服务于作者的判断，而不是替代作者。",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            item {
                AboutSectionHeading("COLOPHON", "产品信息")
            }
            item {
                OutlinedCard(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.outlinedCardColors(containerColor = SimingSurfaceRaised),
                ) {
                    Column(Modifier.padding(horizontal = 17.dp)) {
                        AboutFactRow("当前版本", mobileAboutVersionLabel(BuildConfig.VERSION_NAME))
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        AboutFactRow("开源许可", "Apache 2.0")
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        AboutFactRow("发起与维护", "teangtang1122")
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        AboutFactRow("同步协议", "v${BuildConfig.SYNC_PROTOCOL_VERSION}")
                    }
                }
            }

            item {
                AboutSectionHeading("OUR PRINCIPLES", "三件不愿妥协的事")
            }
            mobileAboutPrinciples.forEach { principle ->
                item(key = principle.number) {
                    OutlinedCard(
                        modifier = Modifier.fillMaxWidth(),
                        colors = CardDefaults.outlinedCardColors(containerColor = SimingSurfaceRaised),
                    ) {
                        Row(
                            modifier = Modifier.padding(17.dp),
                            verticalAlignment = Alignment.Top,
                        ) {
                            Text(
                                text = principle.number,
                                color = SimingCinnabar,
                                style = MaterialTheme.typography.labelLarge,
                                fontWeight = FontWeight.Bold,
                            )
                            Spacer(Modifier.width(15.dp))
                            Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                                Text(principle.title, style = MaterialTheme.typography.titleMedium)
                                Text(
                                    principle.detail,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }

            item {
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(26.dp),
                    color = SimingInk,
                    contentColor = Color.White,
                ) {
                    Column(Modifier.padding(21.dp), verticalArrangement = Arrangement.spacedBy(15.dp)) {
                        AboutKicker("DATA BOUNDARY", color = Color(0xFFE0B567))
                        Text("你的作品，默认留在你选择的位置。", style = MaterialTheme.typography.titleLarge)
                        Text(
                            "司命没有官方小说数据云服务。只有当你主动使用云端 API 或 Gateway 模型时，当前任务所需的提示词、正文片段和上下文才会发送给相应模型提供方。",
                            style = MaterialTheme.typography.bodySmall,
                            color = Color.White.copy(alpha = 0.72f),
                        )
                        DataBoundaryStep("1", "本机作品库", "数据库、离线副本与待同步修改")
                        DataBoundaryStep("2", "按任务筛选", "只组合本次创作所需上下文")
                        DataBoundaryStep("3", "你选择的模型", "数据政策由对应提供方决定")
                    }
                }
            }

            item {
                AboutSectionHeading("OPEN SOURCE & COMMUNITY", "一张持续展开的共同书桌")
            }
            item {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = SimingPaperWarm),
                ) {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.PhoneAndroid, contentDescription = null, tint = SimingCinnabar)
                            Spacer(Modifier.width(10.dp))
                            Column {
                                Text("用户交流 QQ 群", fontWeight = FontWeight.SemiBold)
                                SelectionContainer {
                                    Text(SIMING_QQ_GROUP, color = SimingCinnabar, fontWeight = FontWeight.Bold)
                                }
                            }
                        }
                        Text(
                            "软件本身永久免费，欢迎提交代码、文档、可复现问题与真实创作体验。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            links.forEach { link ->
                item(key = link.url) {
                    OutlinedCard(
                        onClick = { uriHandler.openUri(link.url) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = CardDefaults.outlinedCardColors(containerColor = SimingSurfaceRaised),
                    ) {
                        Row(
                            modifier = Modifier.padding(16.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                                Box(Modifier.size(42.dp), contentAlignment = Alignment.Center) {
                                    Icon(link.icon, contentDescription = null, tint = SimingCinnabar)
                                }
                            }
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(link.label, fontWeight = FontWeight.SemiBold)
                                Text(
                                    link.detail,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Icon(
                                Icons.AutoMirrored.Outlined.ArrowForward,
                                contentDescription = "打开${link.label}",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            item {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Icon(Icons.Outlined.Lock, contentDescription = null, tint = SimingCinnabar)
                    Text(
                        "© ${Year.now().value} teangtang1122 · Apache 2.0",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        "愿每一个故事，都能抵达它应有的结局。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun AboutKicker(text: String, color: Color = SimingCinnabar) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = color,
        fontWeight = FontWeight.Bold,
        letterSpacing = 1.1.sp,
    )
}

@Composable
private fun AboutSectionHeading(kicker: String, title: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        AboutKicker(kicker)
        Text(title, style = MaterialTheme.typography.titleLarge)
    }
}

@Composable
private fun AboutFactRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(value, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun DataBoundaryStep(number: String, title: String, detail: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Surface(shape = CircleShape, color = Color.White.copy(alpha = 0.12f)) {
            Box(Modifier.size(36.dp), contentAlignment = Alignment.Center) {
                Text(number, color = Color(0xFFE0B567), fontWeight = FontWeight.Bold)
            }
        }
        Spacer(Modifier.width(12.dp))
        Column {
            Text(title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = Color.White.copy(alpha = 0.64f))
        }
    }
}
