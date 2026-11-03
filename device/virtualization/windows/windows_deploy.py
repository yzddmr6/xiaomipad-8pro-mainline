# SPDX-License-Identifier: MIT
"""WinPE DISM deployment payload for an owner-provided Windows ISO."""
import base64
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "urn:schemas-microsoft-com:unattend"
WCM = "http://schemas.microsoft.com/WMIConfig/2002/State"
ET.register_namespace("", NS)
ET.register_namespace("wcm", WCM)


def node(parent, tag, value=None, **attrs):
    e = ET.SubElement(parent, "{" + NS + "}" + tag, attrs)
    if value is not None:
        e.text = str(value)
    return e


def component(parent, name):
    return node(
        parent,
        "component",
        name=name,
        processorArchitecture="arm64",
        publicKeyToken="31bf3856ad364e35",
        language="neutral",
        versionScope="nonSxS",
    )


def put(path, text):
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def write_payload(payload: Path, image_index: int, account, instance_id: str):
    if image_index < 1:
        raise ValueError("positive DISM image index required")
    root = ET.Element("{" + NS + "}unattend")
    specialize = node(root, "settings", **{"pass": "specialize"})
    shell = component(specialize, "Microsoft-Windows-Shell-Setup")
    node(shell, "ComputerName", "PIANO-" + instance_id[:8])
    node(shell, "TimeZone", "UTC")
    deploy = component(specialize, "Microsoft-Windows-Deployment")
    sync = node(deploy, "RunSynchronous")
    command = node(sync, "RunSynchronousCommand", **{"{" + WCM + "}action": "add"})
    node(command, "Order", 1)
    node(
        command,
        "Path",
        r"powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\PianoVM\first-boot.ps1",
    )
    oobe = node(root, "settings", **{"pass": "oobeSystem"})
    shell = component(oobe, "Microsoft-Windows-Shell-Setup")
    if account:
        ui = node(shell, "OOBE")
        node(ui, "HideOnlineAccountScreens", "true")
        local = node(
            node(node(shell, "UserAccounts"), "LocalAccounts"),
            "LocalAccount",
            **{"{" + WCM + "}action": "add"},
        )
        node(local, "Name", account["username"])
        node(local, "Group", "Administrators")
        password = node(local, "Password")
        # Windows SIM's LocalAccount hidden-password representation, not encryption.
        encoded = base64.b64encode((account["password"] + "Password").encode("utf-16le")).decode(
            "ascii"
        )
        node(password, "Value", encoded)
        node(password, "PlainText", "false")
    logon = node(
        node(shell, "FirstLogonCommands"), "SynchronousCommand", **{"{" + WCM + "}action": "add"}
    )
    node(logon, "Order", 1)
    node(
        logon,
        "CommandLine",
        r"powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\PianoVM\first-logon.ps1",
    )
    put(
        payload / "first-logon.ps1",
        r"""$ErrorActionPreference='Stop'
foreach ($file in @('C:\PianoVM\unattend.xml','C:\Windows\Panther\unattend.xml','C:\Windows\Panther\Unattend\unattend.xml','C:\Windows\System32\Sysprep\unattend.xml')) {
  if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
}
""",
    )
    put(payload / "unattend.xml", ET.tostring(root, encoding="unicode", xml_declaration=True))
    first = r"""$ErrorActionPreference='Stop'
Start-Transcript C:\PianoVM\first-boot.log
try {
  certutil.exe -addstore -f Root C:\PianoVM\DroidVM_Test.cer
  if ($LASTEXITCODE -ne 0) { throw 'Root certificate import failed' }
  certutil.exe -addstore -f TrustedPublisher C:\PianoVM\DroidVM_Test.cer
  if ($LASTEXITCODE -ne 0) { throw 'Publisher certificate import failed' }
"""
    first += r"""  & C:\PianoVM\winpe-helper.exe --rdma
  if ($LASTEXITCODE -ne 0) { throw 'RDMA interface missing after first boot' }
  & C:\PianoVM\winpe-helper.exe --power --installed
  if ($LASTEXITCODE -ne 0) { throw 'Power device could not be bound' }
"""
    first += r"""  powercfg.exe /hibernate off
  if ($LASTEXITCODE -ne 0) { throw 'Could not disable hibernation' }
  Get-PnpDevice | Where-Object {$_.FriendlyName -match 'VirtIO|Restricted DMA|Gunyah'} | Format-List Status,Class,FriendlyName,InstanceId
  bcdedit.exe /enum '{current}'
"""
    first += "  'PIANO_SPECIALIZE_COMPLETE'\n} catch { Write-Output $_; Stop-Transcript; exit 1 }\nStop-Transcript\nexit 0\n"
    put(payload / "first-boot.ps1", first)
    put(
        payload / "mount-media.txt",
        "select disk 1\nselect partition 1\nassign letter=B\nselect disk 2\nselect partition 1\nremove all noerr\nassign letter=I\n",
    )
    put(
        payload / "partition-system.txt",
        "select disk 0\nclean\nconvert gpt\ncreate partition efi size=260\nformat quick fs=fat32 label=System\nassign letter=S\ncreate partition msr size=16\ncreate partition primary\nformat quick fs=ntfs label=Windows\nassign letter=C\nlist volume\n",
    )
    bootstrap = r"""@echo on
wpeinit
X:\piano\winpe-helper.exe --trust
if errorlevel 1 goto fail
"""
    bootstrap += r"""drvload X:\piano\drivers\rdmapool\rdmapool.inf
if errorlevel 1 goto fail
X:\piano\winpe-helper.exe --rdma
if errorlevel 1 goto fail
"""
    for name, inf in [("viostor", "viostor"), ("NetKVM", "netkvm"), ("vioinput", "vioinput")]:
        bootstrap += f"drvload X:\\piano\\drivers\\{name}\\{inf}.inf\nif errorlevel 1 goto fail\n"
    bootstrap += r"""X:\piano\winpe-helper.exe --power
if errorlevel 1 goto fail
"""
    bootstrap += r"""wpeutil InitializeNetwork
diskpart /s X:\piano\mount-media.txt
if errorlevel 1 goto fail
if not exist B:\PIANOBOOT.TAG goto fail
if not exist I:\install.wim goto fail
call X:\piano\deploy.cmd
if errorlevel 1 goto fail
echo {DEPLOY_TOKEN} >B:\PIANO_DEPLOYED.TAG
if errorlevel 1 goto fail
echo PIANO_DISM_DEPLOYMENT_COMPLETE
wpeutil shutdown
exit /b 0
:fail
echo PIANO_DEPLOYMENT_FAILED - no automatic retry or reboot.
cmd.exe
exit /b 1
"""
    put(payload / "bootstrap.cmd", bootstrap.replace("{DEPLOY_TOKEN}", instance_id))
    commands = [
        "@echo on",
        r"if not exist B:\PIANOBOOT.TAG exit /b 10",
        r"if not exist I:\install.wim exit /b 11",
        f"dism.exe /Get-ImageInfo /ImageFile:I:\\install.wim /Index:{image_index}",
        "if errorlevel 1 exit /b 12",
        r"diskpart /s X:\piano\partition-system.txt > B:\partition.log 2>&1",
        "if errorlevel 1 exit /b 13",
        r"type B:\partition.log",
        r"mkdir C:\PianoScratch",
        f"dism.exe /Apply-Image /ImageFile:I:\\install.wim /Index:{image_index} /ApplyDir:C:\\ /CheckIntegrity /ScratchDir:C:\\PianoScratch /LogPath:B:\\dism-apply.log",
        "if errorlevel 1 exit /b 14",
        r"dism.exe /Image:C:\ /Add-Driver /Driver:X:\piano\drivers /Recurse /ForceUnsigned /ScratchDir:C:\PianoScratch /LogPath:B:\dism-drivers.log",
        "if errorlevel 1 exit /b 15",
        r"dism.exe /Image:C:\ /Get-Drivers /Format:Table /LogPath:B:\dism-driver-list.log > B:\offline-drivers.log",
        "if errorlevel 1 exit /b 16",
        r"call X:\piano\configure-target.cmd > B:\deployment-config.log 2>&1",
        "set CONFIG_RESULT=%ERRORLEVEL%",
        r"type B:\deployment-config.log",
        'if not "%CONFIG_RESULT%"=="0" exit /b %CONFIG_RESULT%',
        r"copy X:\Windows\inf\setupapi.dev.log B:\winpe-setupapi.log",
        r"pnputil /enum-devices /connected /drivers > B:\winpe-drivers.log",
        r"ipconfig /all > B:\winpe-network.log",
        "exit /b 0",
    ]
    put(payload / "deploy.cmd", "\n".join(commands) + "\n")
    config = [
        "@echo on",
        r"xcopy X:\piano C:\PianoVM\ /e /i /h /y",
        "if errorlevel 1 exit /b 20",
        r"if not exist C:\Windows\Panther mkdir C:\Windows\Panther",
        r"copy /y X:\piano\unattend.xml C:\Windows\Panther\unattend.xml",
        "if errorlevel 1 exit /b 21",
        r"bcdboot C:\Windows /s S: /f UEFI",
        "if errorlevel 1 exit /b 22",
    ]
    for option in ["testsigning", "nointegritychecks", "bootlog"]:
        config += [
            f"bcdedit /store S:\\EFI\\Microsoft\\Boot\\BCD /set {{default}} {option} on",
            "if errorlevel 1 exit /b 23",
        ]
    config += [
        r"bcdedit /store S:\EFI\Microsoft\Boot\BCD /enum all",
        r"if not exist S:\EFI\Boot mkdir S:\EFI\Boot",
        r"copy /y S:\EFI\Microsoft\Boot\bootmgfw.efi S:\EFI\Boot\bootaa64.efi",
        "if errorlevel 1 exit /b 24",
        r"reg load HKLM\PianoOffline C:\Windows\System32\config\SYSTEM",
        "if errorlevel 1 exit /b 25",
    ]
    # Keep DISM/INF-created boot driver configuration intact. Log it, do not invent it.
    for control in ["ControlSet001", "ControlSet002"]:
        config += [f"call :existing-control {control}", "if errorlevel 1 goto regfail"]
    config += [
        r"reg query HKLM\PianoOffline\Select",
        r"reg query HKLM\PianoOffline\Setup",
        r"reg unload HKLM\PianoOffline",
        "if errorlevel 1 exit /b 26",
        r"reg load HKLM\PianoSoftware C:\Windows\System32\config\SOFTWARE",
        "if errorlevel 1 exit /b 32",
        r"reg query HKLM\PianoSoftware\Microsoft\Windows\CurrentVersion\Setup\State /v ImageState",
        r"reg unload HKLM\PianoSoftware",
        "if errorlevel 1 exit /b 33",
        "echo PIANO_TARGET_CONFIGURED",
        "exit /b 0",
        ":regfail",
        r"reg unload HKLM\PianoOffline",
        "exit /b 27",
        ":existing-control",
        r"reg query HKLM\PianoOffline\%1 >nul 2>&1",
        "if errorlevel 1 exit /b 0",
        "call :control %1",
        "exit /b %ERRORLEVEL%",
        ":control",
        r"reg add HKLM\PianoOffline\%1\Control\Power /v HibernateEnabled /t REG_DWORD /d 0 /f",
        "if errorlevel 1 exit /b 28",
        r"reg add HKLM\PianoOffline\%1\Control\Power /v HibernateEnabledDefault /t REG_DWORD /d 0 /f",
        "if errorlevel 1 exit /b 28",
    ]
    for driver in ["rdmapool", "viostor"]:
        config += [
            f"reg query HKLM\\PianoOffline\\%1\\Services\\{driver} /s",
            "if errorlevel 1 exit /b 30",
        ]
    config += [
        r"reg query HKLM\PianoOffline\%1\Control\ServiceGroupOrder /v List",
        "if errorlevel 1 exit /b 31",
    ]
    config += ["exit /b 0"]
    put(payload / "configure-target.cmd", "\n".join(config) + "\n")
