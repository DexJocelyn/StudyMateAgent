"""Build a Python-free Windows classroom client installer using built-in Windows components.

The installed client checks the shared host then opens Edge's app window (or the default
browser). PowerShell is invoked with plain -Command, with no policy overrides or encoded code.
"""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent.parent

OPEN=r'''
$ErrorActionPreference='Stop';
Add-Type -AssemblyName System.Windows.Forms;
Add-Type -AssemblyName Microsoft.VisualBasic;
$folder=Join-Path $env:LOCALAPPDATA 'StudyMate\ClassroomClient';
$config=Join-Path $folder 'server.txt';
$base='';
if(Test-Path -LiteralPath $config){$base=[IO.File]::ReadAllText($config).Trim()};
$edit=($env:STUDYMATE_CONFIGURE -eq '1') -or !$base;
function Read-Server([string]$text){
  $u=$null;
  if(![Uri]::TryCreate($text.Trim(),[UriKind]::Absolute,[ref]$u)){throw '请输入完整地址，例如 http://10.0.0.5:8766'};
  if(@('http','https') -notcontains $u.Scheme -or $u.UserInfo -or $u.Query -or $u.Fragment -or $u.AbsolutePath -ne '/'){throw '仅填写主机地址和端口，不要填写账号、密码或项目路径。'};
  $ip=$null;$allowed=$false;
  if([Net.IPAddress]::TryParse($u.DnsSafeHost,[ref]$ip) -and $ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork){
    $b=$ip.GetAddressBytes();$allowed=($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168) -or ($b[0] -eq 127);
  };
  if(!$allowed){throw '教室版只连接局域网地址。请向主机同学索取内网地址。'};
  return $u.GetLeftPart([UriPartial]::Authority);
};
for($attempt=0;$attempt -lt 5;$attempt++){
  try{
    if($edit){$base=[Microsoft.VisualBasic.Interaction]::InputBox('填写Mac主机显示的共享地址，例如 http://10.0.0.5:8766。大家需连接同一个Wi-Fi。','课伴：连接教室主机',$base);if(!$base){exit 0}};
    $base=Read-Server $base;
    $request=[Net.HttpWebRequest]::Create($base+'/healthz');$request.Proxy=$null;$request.Timeout=5000;$request.AllowAutoRedirect=$false;
    try{
      $response=$request.GetResponse();
      try{$reader=New-Object IO.StreamReader($response.GetResponseStream());$data=$reader.ReadToEnd()|ConvertFrom-Json;if($data.ok -ne $true){throw '该地址不是可用的课伴服务。'}}finally{$response.Close()};
    } catch [Net.WebException]{
      $r=$_.Exception.Response;
      if(!$r -or [int]$r.StatusCode -ne 401 -or $r.Headers['WWW-Authenticate'] -notlike '*StudyMate*'){if($r){$r.Close()};throw};
      $r.Close();
    };
    [IO.Directory]::CreateDirectory($folder)|Out-Null;[IO.File]::WriteAllText($config,$base,(New-Object Text.UTF8Encoding($false)));
    $edge=Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe';
    if(!(Test-Path -LiteralPath $edge)){$edge=Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'};
    if(Test-Path -LiteralPath $edge){Start-Process -FilePath $edge -ArgumentList ('--app='+$base)}else{Start-Process $base};
    exit 0;
  } catch{
    try{[IO.Directory]::CreateDirectory($folder)|Out-Null;Add-Content -LiteralPath (Join-Path $folder 'client.log') -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss')+' '+$_.Exception.Message) -Encoding UTF8}catch{};
    $message='未能连接课伴主机。请确认：Mac已开启教室共享、双方连接同一Wi-Fi、地址未变化。校园网若隔离设备，请改连共同热点。错误：'+$_.Exception.Message;
    $choice=[Windows.Forms.MessageBox]::Show($message,'课伴连接提示',[Windows.Forms.MessageBoxButtons]::RetryCancel,[Windows.Forms.MessageBoxIcon]::Information);
    if($choice -ne [Windows.Forms.DialogResult]::Retry){exit 1};$edit=$true;
  };
};
'''

INSTALL=r'''
$ErrorActionPreference='Stop';
Add-Type -AssemblyName System.Windows.Forms;
$source=$env:STUDYMATE_PACKAGE_DIR;
$folder=Join-Path $env:LOCALAPPDATA 'StudyMate\ClassroomClient';
try{
  [IO.Directory]::CreateDirectory($folder)|Out-Null;
  foreach($file in @('打开课伴.cmd','修改主机地址.cmd','卸载课伴.cmd')){Copy-Item -LiteralPath (Join-Path $source $file) -Destination (Join-Path $folder $file) -Force};
  $config=Join-Path $folder 'server.txt';
  if(!(Test-Path -LiteralPath $config)){Copy-Item -LiteralPath (Join-Path $source 'server.txt') -Destination $config};
  $shell=New-Object -ComObject WScript.Shell;
  $desktop=[Environment]::GetFolderPath('Desktop');
  $menu=Join-Path ([Environment]::GetFolderPath('Programs')) '课伴教室版';
  [IO.Directory]::CreateDirectory($menu)|Out-Null;
  foreach($target in @(@($desktop,'课伴教室版','打开课伴.cmd'),@($desktop,'课伴-修改主机地址','修改主机地址.cmd'),@($menu,'课伴教室版','打开课伴.cmd'),@($menu,'修改主机地址','修改主机地址.cmd'),@($menu,'卸载课伴','卸载课伴.cmd'))){
    $shortcut=$shell.CreateShortcut((Join-Path $target[0] ($target[1]+'.lnk')));$shortcut.TargetPath=Join-Path $folder $target[2];$shortcut.WorkingDirectory=$folder;$shortcut.Description='连接教室内共享的课伴项目';$shortcut.Save();
  };
  $choice=[Windows.Forms.MessageBox]::Show('安装完成。桌面已生成“课伴教室版”和“课伴-修改主机地址”。首次连接只需填写主机地址，无需账号密码。现在打开课伴？','课伴安装完成',[Windows.Forms.MessageBoxButtons]::YesNo);
  if($choice -eq [Windows.Forms.DialogResult]::Yes){Start-Process -FilePath (Join-Path $folder '打开课伴.cmd')};
} catch{[Windows.Forms.MessageBox]::Show(('安装失败：'+$_.Exception.Message),'课伴安装提示')|Out-Null;exit 1};
'''

UNINSTALL=r'''
$ErrorActionPreference='Stop';
Add-Type -AssemblyName System.Windows.Forms;
$choice=[Windows.Forms.MessageBox]::Show('卸载本机课伴入口和主机地址设置？主机上的项目、任务和日程会保留。','卸载课伴',[Windows.Forms.MessageBoxButtons]::YesNo);
if($choice -ne [Windows.Forms.DialogResult]::Yes){exit 0};
$folder=Join-Path $env:LOCALAPPDATA 'StudyMate\ClassroomClient';
$desktop=[Environment]::GetFolderPath('Desktop');$menu=Join-Path ([Environment]::GetFolderPath('Programs')) '课伴教室版';
foreach($file in @('课伴教室版.lnk','课伴-修改主机地址.lnk')){$p=Join-Path $desktop $file;if(Test-Path -LiteralPath $p){Remove-Item -LiteralPath $p -Force}};
foreach($file in @('课伴教室版.lnk','修改主机地址.lnk','卸载课伴.lnk')){$p=Join-Path $menu $file;if(Test-Path -LiteralPath $p){Remove-Item -LiteralPath $p -Force}};
foreach($file in @('打开课伴.cmd','修改主机地址.cmd','卸载课伴.cmd','server.txt','client.log')){$p=Join-Path $folder $file;if(Test-Path -LiteralPath $p){Remove-Item -LiteralPath $p -Force}};
foreach($p in @($menu,$folder)){if((Test-Path -LiteralPath $p) -and !(Get-ChildItem -LiteralPath $p -Force)){Remove-Item -LiteralPath $p}};
[Windows.Forms.MessageBox]::Show('本机课伴入口已卸载。','课伴')|Out-Null;
'''

GUIDE='''# 课伴教室版 · Windows 客户端

## 安装与使用

1. Windows 10/11 电脑连接主机所在的同一 Wi-Fi 或热点。
2. 完整解压本压缩包，再双击“安装课伴.cmd”。不需要安装 Python，也不需要管理员权限。
3. 安装后双击桌面“课伴教室版”。填写 Mac 主机提供的地址，教室模式无需账号密码。
4. 选择同一个项目，再选自己的姓名，即可共同操作。其他人修改后，任务和日程看板会在页面空闲时自动更新，也可以点击“刷新”。

Mac 保管数据库、千问和搜索配置。客户端只保存主机地址，不保存模型密钥或项目数据库。
这是安装后连接共享服务的轻量客户端；有 Edge 时以独立应用窗口打开，否则使用默认浏览器。它不把服务器和数据库安装到组员电脑。

## 连不上怎么办

- Mac 先双击“启动教室共享.command”，并保持开机、联网、唤醒。
- Mac 换 Wi-Fi 或地址变化后，双击桌面“课伴-修改主机地址”，重新填写主机显示的地址。
- 同一校园 Wi-Fi 也可能隔离设备；此时让 Mac 和组员共同连接同一个可互访的热点或路由器再试。主机可以上网但组员连不上，不代表安装失败。
- 系统若询问主机是否允许入站连接，仅为正在使用的课伴服务允许所需连接；不要关闭整个防火墙。
- 教室模式免登录，网页里选择自己的姓名用于区分对话与任务归属；不需要注册账号。连接该地址的同网设备也能使用项目和 AI。
- 连接错误日志位于 `%LOCALAPPDATA%\\StudyMate\\ClassroomClient\\client.log`，可交给本机助手排查。

## 适用范围

- 全组连接同一台 Mac，才会使用同一份数据。客户端本身不提供离线模型或离线共享。
- 千问、搜索、天气由 Mac 联网请求；组员无需设置 API Key。
- 已打开的任务和日程看板约每3秒检查一次更新；编辑表单、操作卡片或执行聊天时暂缓更新。对话仍按成员保存，不会把他人的对话插入你的聊天框。
- 局域网 HTTP 下，麦克风语音输入可能受浏览器限制；文字对话、任务和材料操作不依赖麦克风。
- 包内脚本透明可读，未关闭 Windows 防护、未修改 PowerShell 执行策略。此包尚需在 Windows 实机完成安装验收；Mac 上的检查不能替代 Windows 验收。

## 卸载

开始菜单 → 课伴教室版 → 卸载课伴。只移除本机入口与地址设置，保留主机上的项目数据。
'''


def command(script, prefix=''):
    # A single plaintext argument avoids loading unsigned .ps1 files or changing execution policy.
    inline=' '.join(line.strip() for line in script.splitlines() if line.strip())
    if '"' in inline or '%' in inline or len(inline)>7400:
        raise ValueError('PowerShell命令包含未处理的CMD字符或超过长度限制。')
    return ('@echo off\nchcp 65001 >nul\nsetlocal DisableDelayedExpansion\n'+prefix+
            'powershell.exe -NoLogo -NoProfile -STA -Command "'+inline+'"\nexit /b %errorlevel%\n').replace('\n','\r\n').encode('utf-8')


def build(server=''):
    if server:
        import ipaddress
        from urllib.parse import urlsplit
        value=urlsplit(server)
        ip=ipaddress.IPv4Address(value.hostname)
        if value.scheme!='http' or not any(ip in ipaddress.ip_network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')) or value.username or value.password or value.query or value.fragment or value.path not in ('','/'):
            raise ValueError('默认地址必须是局域网HTTP地址。')
        if value.port is None:raise ValueError('请填写主机显示的端口。')
    files={'安装课伴.cmd':command(INSTALL,'set "STUDYMATE_PACKAGE_DIR=%~dp0"\n'),
           '打开课伴.cmd':command(OPEN),
           '修改主机地址.cmd':('@echo off\r\nchcp 65001 >nul\r\nsetlocal DisableDelayedExpansion\r\nset "STUDYMATE_CONFIGURE=1"\r\ncall "%~dp0打开课伴.cmd"\r\n').encode('utf-8'),
           '卸载课伴.cmd':command(UNINSTALL), 'server.txt':(server+'\n').encode('utf-8'),
           '先看这里.txt':GUIDE.encode('utf-8')}
    destination=ROOT/'dist/课伴教室版-Windows客户端安装包.zip'
    destination.parent.mkdir(exist_ok=True)
    manifest={'kind':'Windows classroom client launcher','built_on':'macOS','windows_runtime_verified':False,
              'contains_python':False,'contains_api_keys':False,
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    with ZipFile(destination,'w',ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr('课伴教室版/'+name,data)
        archive.writestr('课伴教室版/文件校验.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name,data in files.items():assert archive.read('课伴教室版/'+name)==data
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--server',default='');args=parser.parse_args()
    print(build(args.server))
