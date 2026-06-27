@echo off
setlocal
set "GGUF=llama3-finetuned.gguf"
if not exist "%~dp0%GGUF%" (
    echo [ERROR] GGUF file not found: %~dp0%GGUF%
    echo [INFO] Run the Unsloth training script first.
    exit /b 1
)
ollama create llama3-finetuned -f "%~dp0Modelfile"
endlocal
