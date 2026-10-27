<#
.SYNOPSIS
  在 MOD 封面底图上合成中文标题与国旗，输出可用于发布页的封面。

.DESCRIPTION
  底图由 media_generate-image 生成，本脚本只做后期合成：
  右下角加标题、暗金副标题、标准国旗，并加一层底部渐变压暗保证文字可读。

  国旗必须传入标准国旗图片文件（3:2，如 1920x1280 的官方旗），
  脚本只做等比缩放，不重绘任何一颗星。手绘五角星的星角朝向与
  四小星对准关系无法凭肉眼还原，缩到 40 像素高后必然失真。

.PARAMETER Cover
  封面底图路径（通常是生成的原图，不带任何文字与国旗）。

.PARAMETER Flag
  标准国旗图片路径，比例应为 3:2。非 3:2 时脚本会报错退出。

.PARAMETER Out
  输出路径。省略时在原图同目录生成 <原名>-cn.png。

.PARAMETER Title
  中文主标题，默认「简体中文汉化」。

.PARAMETER Subtitle
  副标题，通常是 MOD 英文名，默认空（空则不画）。

.PARAMETER TitleSize
  主标题字号（像素），默认 44。

.PARAMETER FlagWidth
  国旗宽度（像素），高度按 3:2 自动计算，默认 60。

.PARAMETER Pad
  右下内边距（像素），默认 60。

.EXAMPLE
  .\compose_cover.ps1 -Cover .\raw.png -Flag .\flag_cn.png `
      -Title '简体中文汉化' -Subtitle 'More Thalmor Dossiers'
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Cover,
  [Parameter(Mandatory)][string]$Flag,
  [string]$Out,
  [string]$Title = '简体中文汉化',
  [string]$Subtitle = '',
  [int]$TitleSize = 44,
  [int]$FlagWidth = 60,
  [int]$Pad = 60,
  [string]$TitleFont = 'STZhongsong',
  [string]$SubFont = 'SimSun'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path -LiteralPath $Cover)) { throw "封面底图不存在：$Cover" }
if (-not (Test-Path -LiteralPath $Flag))  { throw "国旗文件不存在：$Flag" }
if (-not $Out) {
  $dir = Split-Path -Parent (Resolve-Path -LiteralPath $Cover)
  $base = [System.IO.Path]::GetFileNameWithoutExtension($Cover)
  $Out = Join-Path $dir "$base-cn.png"
}

function New-StarPoints {
  # 仅在需要临时绘制时使用；本脚本默认不调用，保留以备无标准旗图时的兜底。
  param($cx, $cy, $r)
  $pts = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
  for ($i = 0; $i -lt 10; $i++) {
    $angle = (-90 + $i * 36) * [Math]::PI / 180
    $rad = if ($i % 2 -eq 0) { $r } else { $r * 0.382 }
    $pts.Add((New-Object System.Drawing.PointF(($cx + $rad * [Math]::Cos($angle)), ($cy + $rad * [Math]::Sin($angle)))))
  }
  return $pts.ToArray()
}

$fi = [System.Drawing.Image]::FromFile($Flag)
$ratio = $fi.Width / $fi.Height
if ([Math]::Abs($ratio - 1.5) -gt 0.02) {
  $fi.Dispose()
  throw "国旗比例不是 3:2（实际 $([Math]::Round($ratio,3))），请换标准旗图，脚本不裁剪不改比例。"
}

$srcImg = [System.Drawing.Image]::FromFile($Cover)
$bmp = New-Object System.Drawing.Bitmap($srcImg)
$srcImg.Dispose()

$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

$W = $bmp.Width; $H = $bmp.Height
$right = $W - $Pad

# 国旗 3:2 等比缩放
$fw = $FlagWidth
$fh = [int][Math]::Round($fw / 1.5)
$fx = $right - $fw
$fy = $H - $Pad - $fh + 2

# 底部渐变压暗：让白字在木纹、深色桌面上都有边界
$gradTop = [int]($H * 0.62)
$rect = New-Object System.Drawing.Rectangle(0, $gradTop, $W, ($H - $gradTop))
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
  (New-Object System.Drawing.Point(0, $gradTop)),
  (New-Object System.Drawing.Point(0, $H)),
  [System.Drawing.Color]::FromArgb(0, 0, 0, 0),
  [System.Drawing.Color]::FromArgb(120, 0, 0, 0))
$g.FillRectangle($brush, $rect)
$brush.Dispose()

# 贴标准国旗
$g.DrawImage($fi, (New-Object System.Drawing.Rectangle($fx, $fy, $fw, $fh)))

# 文字
$fTitle = New-Object System.Drawing.Font($TitleFont, $TitleSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$szT = $g.MeasureString($Title, $fTitle)
$titleX = $fx - 18 - $szT.Width
$titleY = $H - $Pad - $szT.Height + 10

$shadow = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(190, 10, 8, 6))
$white  = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(244, 234, 214))
$dim    = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(206, 176, 122))

if ($Subtitle) {
  $fSub = New-Object System.Drawing.Font($SubFont, [int]($TitleSize * 0.48), [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
  $szS = $g.MeasureString($Subtitle, $fSub)
  $subX = $fx - 18 - $szS.Width
  $subY = $titleY - $szS.Height - 2
  $g.DrawString($Subtitle, $fSub, $shadow, $subX + 2, $subY + 2)
  $g.DrawString($Subtitle, $fSub, $dim,    $subX,     $subY)
  $fSub.Dispose()
}

$g.DrawString($Title, $fTitle, $shadow, $titleX + 2, $titleY + 2)
$g.DrawString($Title, $fTitle, $white,  $titleX,     $titleY)

$g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose(); $fi.Dispose()
$fTitle.Dispose(); $shadow.Dispose(); $white.Dispose(); $dim.Dispose()

[pscustomobject]@{
  Output     = $Out
  Canvas     = "$W x $H"
  TitleXY    = "$([int]$titleX), $([int]$titleY)"
  FlagXY     = "$fx, $fy"
  FlagSize   = "${fw}x${fh}"
  FlagSource = $Flag
} | Format-List
