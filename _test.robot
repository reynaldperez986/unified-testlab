
*** Settings ***
Library    SeleniumLibrary
Library    C:/web__automation/LocatorStatListener.py
...        ${RECORD_ID}    record_name=${RECORD_NAME}
...        folder_name=${PROJECT_FOLDER}    db_url=${DB_URL}
Suite Setup    Open Recorded Application
Suite Teardown    Close All Browsers
Test Teardown    Capture Page Screenshot

*** Variables ***
${BROWSER}          Chrome
${START_URL}        https://demoqa.com/text-box
${RECORD_ID}        10a806e7-aa83-4112-b369-a0485ba1a215
${RECORD_NAME}      TC001 Enter Name
${PROJECT_FOLDER}   Project001
${DEFAULT_TIMEOUT}  10s
${DB_URL}           postgresql://postgres:password@localhost:5432/automation_db

*** Test Cases ***
TC001 Enter Name
    [Documentation]    Generated from session_meta, steps, data, and locators for record_id ${RECORD_ID}.
    Set Test Documentation    Generated from PostgreSQL source tables.
    Execute Recorded Workflow

*** Keywords ***
Open Recorded Application
    Create Webdriver    Chrome    executable_path=C:/web__automation/webdrivers/chromedriver_114.0.5735.90.exe
    Go To    ${START_URL}
    Maximize Browser Window
    Set Selenium Timeout    ${DEFAULT_TIMEOUT}

Execute Recorded Workflow
    Click Element    xpath://*[@id="userName"]
    Input Text       xpath://*[@id="userName"]    Reynald
    Click Element    xpath://*[@id="submit"]