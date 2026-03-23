'
'                        @@@@@@
'                      @@@@@@@@@@
'                     @@@     @@@@
'                      @@@@  @@@
'                   @@@ @@@@@@ @@@
'                  @@@@@@@@@@@@@@@@
'                 @@@    @@@@   @@@@
'                  @@@@@@    @@@@@
'                @@@@@@@@@@@@@@@@@@@@
'               @@@@@@@@@@@@@@@@@@@@@@@
'              @@@@@@@@@@@@@@@@@@@@@@@@@
'             @@@@@@  O @@@@@@  O  @@@@@@
'             @@@@@@ .O.@@@@@@.O.  @@@@@@
'             @@@@@@@@@@@@@@@@@@@@@@@@@@@
'             @@@@@@                @@@@@@
'             @@@@@@ \            / @@@@@@
'              @@@@@@ \  @@@@@@  / @@@@@@
'              @@@@@@@ \________/ @@@@@@@
'               @@@@@@@@@@@@@@@@@@@@@@@
'                @@@@@@@@@@@@@@@@@@@@@
'                 @@@@@@@@@@@@@@@@@@@
'                   @@@@@@@@@@@@@@@
'
'     ╦ ╦╦╔╗╔╔╦╗╔═╗╦ ╦╔═╗  ╔╦╗╔═╗╦  ╦  ╦╔═╗  ╔═╗╦ ╦╦╔╦╗
'     ║║║║║║║ ║║║ ║║║║╚═╗   ║║║╣ ╚╗╔╝  ║╚═╗  ╚═╗╠═╣║ ║
'     ╚╩╝╩╝╚╝═╩╝╚═╝╚╩╝╚═╝  ═╩╝╚═╝ ╚╝   ╩╚═╝  ╚═╝╩ ╩╩ ╩
'

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' --- Create / update desktop shortcut pointing to this script ---
Dim desktopPath, lnkPath, scriptPath
desktopPath = ws.SpecialFolders("Desktop")
scriptPath = fso.GetAbsolutePathName(WScript.ScriptFullName)

' Remove any existing LoL Crawler shortcut
For Each f In fso.GetFolder(desktopPath).Files
    If LCase(fso.GetExtensionName(f.Name)) = "lnk" Then
        If InStr(1, LCase(f.Name), "lol crawler", vbTextCompare) > 0 Or _
           InStr(1, LCase(f.Name), "lol-crawler", vbTextCompare) > 0 Then
            f.Delete True
        End If
    End If
Next

' Create fresh shortcut
Set lnk = ws.CreateShortcut(desktopPath & "\LoL Crawler Dev.lnk")
lnk.TargetPath = "wscript.exe"
lnk.Arguments = """" & scriptPath & """"
lnk.WorkingDirectory = fso.GetParentFolderName(scriptPath)
lnk.Description = "Launch LoL Crawler dev container"
lnk.Save

' --- Resolve repo root (parent of extras/) and convert to WSL path ---
Dim repoWin, repoWsl
repoWin = fso.GetParentFolderName(fso.GetParentFolderName(scriptPath))
' C:\Users\foo\Desktop\LoL-Crawler -> /mnt/c/Users/foo/Desktop/LoL-Crawler
repoWsl = Replace(repoWin, "\", "/")
repoWsl = "/mnt/" & LCase(Left(repoWsl, 1)) & Mid(repoWsl, 3)

' --- Start Docker Desktop if not running ---
ws.Run """C:\Program Files\Docker\Docker\Docker Desktop.exe""", 0, False

' Wait up to 120s for Docker daemon using the Linux docker CLI in WSL
For i = 1 To 60
    WScript.Sleep 2000
    ret = ws.Run("wsl -d Ubuntu-24.04 -e bash -lc ""docker info > /dev/null 2>&1""", 0, True)
    If ret = 0 Then Exit For
Next

' Open VS Code directly in the dev container — no manual "Reopen" needed
Dim devContainerUri
devContainerUri = "vscode-remote://dev-container+%s/workspace"

' Dev Containers extension expects the hex-encoded local folder path
Dim hexPath, i, c
hexPath = ""
For i = 1 To Len(repoWin)
    c = Mid(repoWin, i, 1)
    hexPath = hexPath & Right("0" & Hex(Asc(c)), 2)
Next
devContainerUri = Replace(devContainerUri, "%s", LCase(hexPath))

ws.Run "code --folder-uri """ & devContainerUri & """", 0, False
