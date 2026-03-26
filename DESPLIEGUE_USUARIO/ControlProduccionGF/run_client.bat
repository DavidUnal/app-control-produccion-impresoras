@echo off
set "PUBLIC_BASE_URL=http://172.16.20.215:8000"
set "GOOGLE_CLIENT_ID=326829798148-r5def3v6se5b0j9nseat63sq3ah68728.apps.googleusercontent.com"
set "GOOGLE_CLIENT_SECRET=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"
set "GOOGLE_ALLOWED_HD="
cd /d "%~dp0"
start "" "%~dp0ControlProduccionGF.exe"
