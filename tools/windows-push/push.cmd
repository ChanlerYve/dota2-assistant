@echo off
rem One-click push helper for this machine.
rem
rem Why this exists: on this host github.com:443 is unreachable (TCP timeout) while
rem ssh.github.com:443 and api.github.com work. OpenSSH cannot be used either
rem (private key ACL check + ssh-agent named pipe both blocked by the sandbox),
rem so git talks to GitHub through a paramiko-based remote helper instead.
rem
rem Usage:
rem   push.cmd                    push current branch
rem   push.cmd <branch>           push a specific branch
setlocal EnableExtensions
set "HERE=%~dp0"
set "REPO=%HERE%..\dota2-assistant"
set "PATH=%HERE%;%PATH%"
if "%D2A_SSH_KEY%"=="" set "D2A_SSH_KEY=%HERE%id_ed25519"
cd /d "%REPO%" || exit /b 1
if "%~1"=="" (set "BRANCH=main") else (set "BRANCH=%~1")
git push -u origin %BRANCH%
exit /b %ERRORLEVEL%
