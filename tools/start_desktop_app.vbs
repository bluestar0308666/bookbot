Option Explicit

Dim shell, files, scriptFolder, powerShellScript, powerShellExe, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

scriptFolder = files.GetParentFolderName(WScript.ScriptFullName)
powerShellScript = files.BuildPath(scriptFolder, "start_desktop_app.ps1")
powerShellExe = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = """" & powerShellExe & """ -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & powerShellScript & """"

shell.Run command, 0, False
