@echo off
setlocal
REM ===================== CONFIG =====================
set "PSQL_PATH=psql"
set "PGHOST=localhost"
set "PGPORT=5432"
set "PGDATABASE=postgres"
set "PGUSER=postgres"

REM CAUTION: Storing passwords in plain text is risky. Consider using secrets management.
set "PGPASSWORD=admin@10182024"
set "NEW_PASSWORD=password"

REM Optional logging (uncomment the next two lines if you want a log)
:: set "LOGFILE=%~dpn0.log"
:: call :log ---------- Run started at %DATE% %TIME% ----------

REM ===================== CHECK PSQL EXISTS =====================
where "%PSQL_PATH%" >nul 2>&1
if errorlevel 1 (
    echo FAIL: psql not found on PATH or PSQL_PATH is incorrect: "%PSQL_PATH%"
    REM call :log FAIL: psql not found on PATH or PSQL_PATH is incorrect: "%PSQL_PATH%"
    exit /b 2
)

REM ===================== ESCAPE SINGLE QUOTES IN NEW PASSWORD =====================
setlocal EnableDelayedExpansion
set "ESCAPED_NEW=!NEW_PASSWORD:'='''!"
endlocal & set "ESCAPED_NEW=%ESCAPED_NEW%"

REM ===================== CHANGE PASSWORD =====================
"%PSQL_PATH%" -h "%PGHOST%" -p "%PGPORT%" -U "%PGUSER%" -d "%PGDATABASE%" -v ON_ERROR_STOP=1 -q ^
  -c "ALTER ROLE \"%PGUSER%\" WITH PASSWORD '%ESCAPED_NEW%';"
set "ALTER_RC=%ERRORLEVEL%"

if %ALTER_RC% neq 0 (
    echo FAIL: ALTER ROLE command failed (errorlevel=%ALTER_RC%)
    REM call :log FAIL: ALTER ROLE command failed (errorlevel=%ALTER_RC%)
    call :secure_cleanup
    exit /b 1
) else (
    echo OK: Password change command executed.
    REM call :log OK: Password change command executed.
)

REM ===================== VERIFY NEW PASSWORD =====================
REM Try to connect using the NEW password. \q exits immediately.
set "PGPASSWORD=%NEW_PASSWORD%"
"%PSQL_PATH%" -h "%PGHOST%" -p "%PGPORT%" -U "%PGUSER%" -d "%PGDATABASE%" -q -c "\q"
set "VERIFY_RC=%ERRORLEVEL%"

if %VERIFY_RC% neq 0 (
    echo FAIL: Verification login with NEW password failed (errorlevel=%VERIFY_RC%)
    REM call :log FAIL: Verification login with NEW password failed (errorlevel=%VERIFY_RC%)
    call :secure_cleanup
    exit /b 3
) else (
    echo PASS: Password successfully changed and verified.
    REM call :log PASS: Password successfully changed and verified.
)

REM ===================== CLEANUP AND EXIT =====================
call :secure_cleanup
exit /b 0

REM ===================== FUNCTIONS =====================
:secure_cleanup
    REM Always clear sensitive variables
    set "PGPASSWORD="
    set "NEW_PASSWORD="
    set "ESCAPED_NEW="
    endlocal & rem (ends the initial setlocal)
    goto :eof

:log
    REM Simple logger (requires LOGFILE to be set and uncommented above)
    REM >> "%LOGFILE%" echo [%DATE% %TIME%] %*
    goto :eof