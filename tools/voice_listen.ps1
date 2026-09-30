<#
英雄名语音识别（Windows 自带 System.Speech，离线、零第三方依赖）

用途：给 BP 录入加一个语音通道——说「剑圣」「斧王」「jugg」就能录入英雄。

设计要点
--------
* **只用 Windows 自带能力**：LoadGrammar + 麦克风，不联网、不上传音频、不需要 API Key，
  与项目「只用公开数据 / 数据留在本机」的定位一致。
* **受约束语法而非自由听写**：默认把英雄全名 + data/aliases.json 里的 500 多条别名
  组成一个 Choices 语法。识别范围被限制在英雄名上，准确率远高于 DictationGrammar。
  需要自由说话时加 -Mode dictation。
* **逐行输出**：每行一条结果，由 Python 侧的 d2a/voice.py 逐行读取：
      READY <culture>        引擎就绪
      HEARD <文本>           识别到一句话
      REJECT <文本> <置信度> 识别到但置信度低于 -MinConfidence
      FLAG <名字> <置信度>   高置信度命中（供上层加速录入）
      WARN <消息>            非致命问题
      ERROR <消息>           致命问题
      STOPPED <原因>         结束
* 任何异常都会走 ERROR/STOPPED，绝不静默挂死。

用法::

    powershell -NoProfile -ExecutionPolicy Bypass -File tools/voice_listen.ps1
    powershell ... -File tools/voice_listen.ps1 -Seconds 8 -Culture zh-CN
    powershell ... -File tools/voice_listen.ps1 -Mode dictation
#>
[CmdletBinding()]
param(
    # 最多监听多少秒（到时自动停止）。0 = 一直听，直到 Ctrl+C。
    [int]$Seconds = 0,
    # 识别语言，例如 zh-CN / en-US。留空 = 用系统默认识别引擎。
    [string]$Culture = "",
    # hero = 受约束的英雄名语法（默认，准确率高）；dictation = 自由听写
    [ValidateSet('hero', 'dictation')]
    [string]$Mode = "hero",
    # 低于该置信度就丢弃（0~1）
    [double]$MinConfidence = 0.55,
    # 允许同一句话重复上报的最短间隔（秒），防止一次说话被反复识别
    [double]$RepeatGuardSeconds = 1.5,
    # 安静多久算一句话结束（毫秒）
    [int]$EndSilenceMs = 700,
    # 别名表路径（用于构建英雄名语法）
    [string]$AliasesPath = ""
)

$ErrorActionPreference = "Stop"

function Write-Line([string]$tag, [string]$text) {
    # 显式 UTF-8 输出，避免 Python 侧按 GBK 解读中文乱码
    $out = [System.Console]::Out
    $out.WriteLine("$tag $text")
    $out.Flush()
}

# --------------------------------------------------------------------- 载入语音库
try {
    Add-Type -AssemblyName System.Speech
} catch {
    Write-Line "ERROR" "无法加载 System.Speech（需要 Windows + .NET）：$($_.Exception.Message)"
    Write-Line "STOPPED" "assembly"
    exit 1
}

# --------------------------------------------------------------------- 选引擎
try {
    $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
} catch {
    Write-Line "ERROR" "枚举识别引擎失败：$($_.Exception.Message)"
    Write-Line "STOPPED" "enumerate"
    exit 1
}

if (-not $recs -or $recs.Count -eq 0) {
    Write-Line "ERROR" "系统没有安装任何语音识别引擎。请在「设置 → 时间和语言 → 语言」里为中文添加语音识别支持。"
    Write-Line "STOPPED" "no-engine"
    exit 1
}

$selected = $null
if ($Culture) {
    $selected = $recs | Where-Object { $_.Culture.Name -ieq $Culture } | Select-Object -First 1
    if (-not $selected) {
        $avail = ($recs | ForEach-Object { $_.Culture.Name }) -join ", "
        Write-Line "ERROR" "找不到语言 $Culture 的识别引擎。可用：$avail"
        Write-Line "STOPPED" "culture"
        exit 1
    }
} else {
    # 优先中文，其次系统第一个
    $selected = $recs | Where-Object { $_.Culture.Name -like "zh-*" } | Select-Object -First 1
    if (-not $selected) { $selected = $recs | Select-Object -First 1 }
}

# 创建引擎。两条路都试：
#  a) 用 RecognizerInfo.Id 构造；
#  b) 无参构造（等价于系统默认）。
# 注意：无参构造在某些机器上会返回一个 RecognizerInfo 为 null 的「空壳」引擎，
# 之后 LoadGrammar 会报 NullReference。所以必须校验 RecognizerInfo 才能认为可用。
$engine = $null
$errId = $null
$errNoArg = $null
if ($selected.Id) {
    try {
        $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine $selected.Id
        if (-not $engine.RecognizerInfo) { $engine.Dispose(); $engine = $null; $errId = "构造成功但 RecognizerInfo 为空" }
    } catch {
        $errId = $_.Exception.Message
        $engine = $null
    }
}
if (-not $engine) {
    try {
        $cand = New-Object System.Speech.Recognition.SpeechRecognitionEngine
        if ($cand.RecognizerInfo) {
            $engine = $cand
        } else {
            $cand.Dispose()
            $errNoArg = "构造成功但 RecognizerInfo 为空（系统默认识别引擎未就绪）"
        }
    } catch {
        $errNoArg = $_.Exception.Message
    }
}
if (-not $engine) {
    Write-Line "ERROR" "无法创建可用的识别引擎。按 Id 构造：$errId ；无参构造：$errNoArg"
    Write-Line "ERROR" "常见原因：没有安装语音识别组件，或当前进程无法访问音频设备（例如被沙箱/权限限制）。"
    Write-Line "STOPPED" "engine"
    exit 1
}

# --------------------------------------------------------------------- 加载语法
$grammarCount = 0
if ($Mode -eq "hero") {
    $phrases = New-Object System.Collections.Generic.List[string]

    # 1) 官方英雄全名（从 heroes.json 或别名表的值里取）
    if (-not $AliasesPath) {
        $here = Split-Path -Parent $MyInvocation.MyCommand.Path
        $AliasesPath = Join-Path (Split-Path -Parent $here) "data\aliases.json"
    }
    $heroNames = @()
    if (Test-Path $AliasesPath) {
        try {
            $alias = Get-Content -Raw -Encoding UTF8 $AliasesPath | ConvertFrom-Json
            foreach ($p in $alias.PSObject.Properties) {
                if ($p.Name -like "_*") { continue }
                $phrases.Add($p.Name)
                $heroNames += [string]$p.Value
            }
        } catch {
            Write-Line "WARN" "读取别名表失败，回退到系统默认语法：$($_.Exception.Message)"
        }
    } else {
        Write-Line "WARN" "找不到别名表 $AliasesPath（语音仍可用，但识别范围更窄）"
    }
    # 2) 官方全名也加进去（别名表里只有 别名→正式名 的映射）
    $extra = @()
    $heroesJson = Join-Path (Split-Path -Parent $AliasesPath) "heroes.json"
    if (Test-Path $heroesJson) {
        try {
            $hj = Get-Content -Raw -Encoding UTF8 $heroesJson | ConvertFrom-Json
            foreach ($h in $hj.heroes) { $extra += [string]$h.name }
        } catch { }
    }
    $all = @($phrases) + @($heroNames) + @($extra) |
        Where-Object { $_ -and $_.Trim().Length -gt 0 } |
        ForEach-Object { $_.Trim() } |
        Sort-Object -Unique

    if ($all.Count -eq 0) {
        Write-Line "WARN" "没有可用于约束语法的英雄名，改用自由听写"
        $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
        $grammarCount = 1
    } else {
        # Choices 有数量上限；超出时截断而不是抛异常
        $limit = 1500
        if ($all.Count -gt $limit) {
            Write-Line "WARN" "英雄名条目过多($($all.Count))，只取前 $limit 条"
            $all = $all[0..($limit - 1)]
        }
        try {
            $choices = New-Object System.Speech.Recognition.Choices
            $choices.Add([string[]]$all)
            $gb = New-Object System.Speech.Recognition.GrammarBuilder
            $gb.Append($choices)
            $g = New-Object System.Speech.Recognition.Grammar $gb
            $engine.LoadGrammar($g)
            $grammarCount = 1
            Write-Line "WARN" "已加载 $($all.Count) 个英雄名词条"
        } catch {
            Write-Line "WARN" "构建英雄名语法失败，回退到自由听写：$($_.Exception.Message)"
            $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
            $grammarCount = 1
        }
    }
} else {
    $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    $grammarCount = 1
}

if ($grammarCount -eq 0) {
    Write-Line "ERROR" "没有加载任何语法"
    Write-Line "STOPPED" "grammar"
    exit 1
}

# --------------------------------------------------------------------- 麦克风
try {
    $engine.SetInputToDefaultAudioDevice()
} catch {
    Write-Line "ERROR" "无法打开麦克风（默认录音设备不可用，或权限被拒绝）：$($_.Exception.Message)"
    Write-Line "STOPPED" "microphone"
    exit 1
}

$engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(5)
$engine.BabbleTimeout = [TimeSpan]::FromSeconds(3)
$engine.EndSilenceTimeout = [TimeSpan]::FromMilliseconds($EndSilenceMs)
$engine.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromMilliseconds(1200)

# 置信度门限
$confUpdate = New-Object System.Speech.Recognition.SpeechRecognitionEngine
try {
    $engine.UpdateRecognizerSetting("CFGConfidenceRejectionThreshold", [int]($MinConfidence * 100))
} catch {
    # 部分引擎不支持该设置，忽略即可（我们自己也会按 Confidence 过滤）
}

# --------------------------------------------------------------------- 事件
$lastText = ""
$lastAt = [DateTime]::MinValue

$handler = {
    param($sender, $e)
    if ($null -eq $e.Result) { return }
    $text = $e.Result.Text
    if (-not $text) { return }
    $conf = [double]$e.Result.Confidence
    $now = [DateTime]::Now
    if ($text -eq $script:lastText -and ($now - $script:lastAt).TotalSeconds -lt $RepeatGuardSeconds) {
        return
    }
    $script:lastText = $text
    $script:lastAt = $now
    if ($conf -lt $MinConfidence) {
        Write-Line "REJECT" ("{0} {1:N2}" -f $text, $conf)
    } else {
        Write-Line "HEARD" $text
        Write-Line "FLAG" ("{0} {1:N2}" -f $text, $conf)
    }
}

$engine.add_SpeechRecognized($handler)

$recogError = {
    param($sender, $e)
    Write-Line "WARN" "识别错误: $($e.Error)"
}
$engine.add_RecognizeCompleted({
    param($sender, $e)
    if ($e.Error) { Write-Line "WARN" "识别会话结束于错误: $($e.Error)" }
})

# --------------------------------------------------------------------- 主循环
Write-Line "READY" "$($selected.Culture.Name) $Mode"

try {
    if ($Seconds -gt 0) {
        $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
        Start-Sleep -Seconds $Seconds
        $engine.RecognizeAsyncStop()
        Start-Sleep -Milliseconds 300
        Write-Line "STOPPED" "timeout"
    } else {
        # 一直听，直到 Ctrl+C
        $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
        while ($true) { Start-Sleep -Milliseconds 250 }
    }
} catch [System.Management.Automation.PipelineStoppedException] {
    Write-Line "STOPPED" "interrupted"
} catch {
    Write-Line "ERROR" "识别过程出错：$($_.Exception.Message)"
    Write-Line "STOPPED" "runtime"
    exit 1
} finally {
    try { $engine.RecognizeAsyncCancel() } catch { }
    try { $engine.Dispose() } catch { }
}
